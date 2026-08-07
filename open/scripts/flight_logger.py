#!/usr/bin/env python3
"""
Diff-Navigation 飞行日志后台服务

自动记录每次飞行的:
  - ROS 节点日志 (/rosout → 按节点分文件 + 统一日志)
  - 关键话题健康状态 (频率/断连 → topic_health.csv)
  - 节点生命周期 (上线/下线/崩溃 → events.jsonl)
  - 元数据 (时间/git/配置快照 → metadata.yaml)

启动方式:
  自动: navigation.sh 在启动节点前自动拉起
  手动: python3 scripts/flight_logger.py
  停止: kill -TERM <pid>  或  python3 scripts/log_cli.py stop
"""

import sys
import os
import time
import signal
import struct
import json
import csv
import shutil
import subprocess
from datetime import datetime
from collections import deque

# --- 将项目 scripts 目录加入 path, 以便 import log_common ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

from log_common import (
    detect_localization_mode, get_logging_config,
    get_monitor_topics, get_monitor_nodes,
    TopicHzTracker,
)

# ============================================================================
# 路径常量
# ============================================================================
LOGS_ROOT = os.path.join(PROJECT_ROOT, 'logs')
PID_FILE = os.path.join(LOGS_ROOT, '.flight_logger.pid')
STATE_FILE = os.path.join(LOGS_ROOT, '.flight_logger_state.json')

# ============================================================================
# 存储管理器
# ============================================================================

class StorageManager:
    """管理日志存储: 容量检查、旧日志清理、会话目录创建"""

    def __init__(self, max_size_gb=2.0, min_free_mb=500, session_retention=50):
        self.max_size_bytes = int(max_size_gb * 1024 * 1024 * 1024)
        self.min_free_bytes = int(min_free_mb * 1024 * 1024)
        self.session_retention = session_retention

    def ensure_logs_root(self):
        os.makedirs(LOGS_ROOT, exist_ok=True)

    def create_session_dir(self, session_id):
        path = os.path.join(LOGS_ROOT, session_id)
        os.makedirs(path, exist_ok=True)
        # 按节点分文件的子目录
        os.makedirs(os.path.join(path, 'per_node'), exist_ok=True)
        return path

    def get_dir_size(self, path):
        """递归计算目录大小 (bytes)"""
        total = 0
        try:
            for dirpath, _, filenames in os.walk(path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    try:
                        total += os.path.getsize(fp)
                    except OSError:
                        pass
        except OSError:
            pass
        return total

    def check_disk_space(self):
        """检查磁盘剩余空间，返回 (ok, warning_msg)"""
        try:
            stat = os.statvfs(LOGS_ROOT)
            free = stat.f_bavail * stat.f_frsize
            if free < self.min_free_bytes:
                return False, (
                    f'磁盘剩余空间不足 ({free / 1024 / 1024:.0f}MB < {self.min_free_bytes / 1024 / 1024:.0f}MB)，'
                    f'已暂停新日志写入并保留最近一次完整日志'
                )
            return True, ''
        except OSError:
            return True, ''  # 无法检测时默认放行

    def list_sessions(self):
        """列出所有会话目录，按时间排序 (旧→新)"""
        if not os.path.isdir(LOGS_ROOT):
            return []
        sessions = []
        for name in os.listdir(LOGS_ROOT):
            if name.startswith('.') or name.startswith('_'):
                continue
            path = os.path.join(LOGS_ROOT, name)
            if os.path.isdir(path):
                mtime = os.path.getmtime(path)
                sessions.append((name, path, mtime))
        sessions.sort(key=lambda x: x[2])
        return sessions

    def is_session_kept(self, session_path):
        """检查会话是否标记为保留"""
        meta = os.path.join(session_path, 'metadata.yaml')
        if os.path.exists(meta):
            try:
                with open(meta, 'r') as f:
                    content = f.read()
                return 'keep: true' in content
            except Exception:
                pass
        return False

    def cleanup_old_sessions(self, dry_run=False):
        """
        清理旧日志: 先按容量上限, 再按场次数量上限。
        跳过标记为 keep 的会话。返回清理的会话列表。
        """
        removed = []
        sessions = self.list_sessions()
        if not sessions:
            return {
                'removed': [],
                'remaining_total': 0,
                'remaining_kept': [],
                'remaining_normal': [],
                'total_size_gb': 0.0,
            }

        # 策略 1: 按容量清理
        total_size = self.get_dir_size(LOGS_ROOT)
        idx = 0
        while total_size > self.max_size_bytes and idx < len(sessions):
            name, path, _ = sessions[idx]
            if self.is_session_kept(path):
                idx += 1
                continue
            size = self.get_dir_size(path)
            if not dry_run:
                shutil.rmtree(path)
            removed.append(name)
            total_size -= size
            idx += 1

        # 策略 2: 按场次数量清理
        kept_sessions = [s for s in self.list_sessions() if not self.is_session_kept(s[1])]
        while len(kept_sessions) > self.session_retention:
            name, path, _ = kept_sessions[0]
            if not dry_run:
                shutil.rmtree(path)
            removed.append(name)
            kept_sessions.pop(0)

        # 重新列出剩余会话
        remaining = self.list_sessions()
        kept = [s[0] for s in remaining if self.is_session_kept(s[1])]
        normal = [s[0] for s in remaining if not self.is_session_kept(s[1])]
        total = self.get_dir_size(LOGS_ROOT) if not dry_run else total_size

        return {
            'removed': removed,
            'remaining_total': len(remaining),
            'remaining_kept': kept,
            'remaining_normal': normal,
            'total_size_gb': total / 1024 / 1024 / 1024,
        }

    def warn_if_near_limit(self):
        """接近上限时返回警告消息"""
        total_size = self.get_dir_size(LOGS_ROOT)
        if total_size > self.max_size_bytes * 0.85:
            return (
                f'日志存储已接近上限 ({total_size / 1024 / 1024 / 1024:.1f}GB / '
                f'{self.max_size_bytes / 1024 / 1024 / 1024:.1f}GB)，请及时清理或导出旧日志'
            )
        return ''


# ============================================================================
# 元数据记录器
# ============================================================================

class MetadataWriter:
    """收集并写入会话元数据"""

    @staticmethod
    def collect_metadata(session_id, start_time):
        meta = {
            'session_id': session_id,
            'start_time': datetime.fromtimestamp(start_time).isoformat(),
            'start_unix': start_time,
            'end_time': None,
            'end_unix': None,
            'duration_seconds': None,
            'localization_mode': detect_localization_mode(),
            'keep': False,
        }

        # git 信息
        try:
            meta['git_commit'] = subprocess.check_output(
                ['git', '-C', PROJECT_ROOT, 'rev-parse', 'HEAD'],
                stderr=subprocess.DEVNULL
            ).decode().strip()
            meta['git_branch'] = subprocess.check_output(
                ['git', '-C', PROJECT_ROOT, 'rev-parse', '--abbrev-ref', 'HEAD'],
                stderr=subprocess.DEVNULL
            ).decode().strip()
            meta['git_dirty'] = bool(subprocess.check_output(
                ['git', '-C', PROJECT_ROOT, 'status', '--porcelain'],
                stderr=subprocess.DEVNULL
            ).decode().strip())
        except Exception:
            meta['git_commit'] = 'unknown'
            meta['git_branch'] = 'unknown'
            meta['git_dirty'] = None

        # 配置快照
        config_path = os.path.join(PROJECT_ROOT, 'config', 'navigation_config.yaml')
        if os.path.exists(config_path):
            try:
                meta['config_file'] = 'navigation_config.yaml'
            except Exception:
                meta['config_file'] = None
        else:
            meta['config_file'] = None

        # 无人机 ID (如果配置了)
        try:
            from log_common import load_navigation_config
            cfg = load_navigation_config()
            drone = cfg.get('drone', {})
            if drone:
                meta['drone_id'] = drone.get('id', 'unknown')
                meta['drone_type'] = drone.get('type', 'unknown')
        except Exception:
            pass

        return meta

    @staticmethod
    def write_metadata(session_path, meta):
        import yaml
        path = os.path.join(session_path, 'metadata.yaml')
        with open(path, 'w') as f:
            yaml.dump(meta, f, default_flow_style=False, allow_unicode=True)

    @staticmethod
    def finalize_metadata(session_path, end_time, flight_start_time=None, flight_end_time=None):
        """写入结束时间、飞行时长和脚本运行时长"""
        import yaml
        path = os.path.join(session_path, 'metadata.yaml')
        if not os.path.exists(path):
            return
        with open(path, 'r') as f:
            meta = yaml.safe_load(f) or {}
        start = meta.get('start_unix')
        meta['end_time'] = datetime.fromtimestamp(end_time).isoformat()
        meta['end_unix'] = end_time
        if start:
            meta['script_duration_seconds'] = round(end_time - start, 1)
        if flight_start_time:
            f_end = flight_end_time if flight_end_time else end_time
            meta['flight_start_time'] = datetime.fromtimestamp(flight_start_time).isoformat()
            meta['flight_start_unix'] = flight_start_time
            meta['flight_end_time'] = datetime.fromtimestamp(f_end).isoformat()
            meta['flight_end_unix'] = f_end
            meta['duration_seconds'] = round(f_end - flight_start_time, 1)
        else:
            meta['duration_seconds'] = None
        with open(path, 'w') as f:
            yaml.dump(meta, f, default_flow_style=False, allow_unicode=True)

    @staticmethod
    def save_config_snapshot(session_path):
        """保存当前配置文件的快照"""
        config_path = os.path.join(PROJECT_ROOT, 'config', 'navigation_config.yaml')
        if os.path.exists(config_path):
            dst = os.path.join(session_path, 'config_snapshot.yaml')
            shutil.copy2(config_path, dst)


# ============================================================================
# ROS 日志收集器
# ============================================================================

class LogCollector:
    """
    订阅 /rosout 收集所有节点日志，写入:
      - logs/<session>/per_node/<node_name>.log   (按节点分文件)
      - logs/<session>/rosout.log                 (统一日志，带[节点名]前缀)
    """

    # 日志级别映射
    LEVEL_NAMES = {1: 'DEBUG', 2: 'INFO', 4: 'WARN', 8: 'ERROR', 16: 'FATAL'}

    def __init__(self, session_path):
        self._session_path = session_path
        self._per_node_dir = os.path.join(session_path, 'per_node')
        self._rosout_path = os.path.join(session_path, 'rosout.log')
        self._node_files = {}    # node_name → file handle
        self._rosout_file = None
        self._start_time = time.time()

    def open(self):
        self._rosout_file = open(self._rosout_path, 'a', buffering=1)  # 行缓冲, 每条立即落盘

    def on_rosout_msg(self, msg):
        """处理 /rosout 消息，兼容 typed msg 和 AnyMsg"""
        try:
            # 提取字段 (兼容 typed 和 AnyMsg)
            name = getattr(msg, 'name', 'unknown')
            text = getattr(msg, 'msg', str(msg))
            level = getattr(msg, 'level', 2)

            level_name = self.LEVEL_NAMES.get(level, f'L{level}')
            timestamp = time.time()
            ts_str = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

            line = f'[{ts_str}] [{level_name}] [{name}] {text}\n'

            # 写入统一 rosout.log
            if self._rosout_file:
                self._rosout_file.write(line)

            # 写入按节点分文件
            if name not in self._node_files:
                safe_name = name.replace('/', '_').strip('_')
                node_path = os.path.join(self._per_node_dir, f'{safe_name}.log')
                self._node_files[name] = open(node_path, 'a', buffering=1)
            self._node_files[name].write(line)

        except Exception:
            pass  # 静默处理，日志工具绝不能影响飞控

    def close(self):
        try:
            if self._rosout_file:
                self._rosout_file.close()
        except Exception:
            pass
        for name, fh in self._node_files.items():
            try:
                fh.close()
            except Exception:
                pass
        self._node_files.clear()


# ============================================================================
# 话题健康记录器
# ============================================================================

class TopicHealthRecorder:
    """
    记录关键话题的健康状态到 CSV。
    每 5 秒采样一次，写入: timestamp, topic, expected_hz, actual_hz, status
    """

    def __init__(self, session_path, loc_mode=None):
        self._session_path = session_path
        self._csv_path = os.path.join(session_path, 'topic_health.csv')
        self._events_path = os.path.join(session_path, 'events.jsonl')
        self._topics = get_monitor_topics(loc_mode)
        self._tracker = TopicHzTracker(window=3.0)
        self._prev_status = {}   # topic → previous status (用于检测状态变化)
        self._csv_file = None
        self._csv_writer = None
        self._sample_count = 0

    def open(self):
        self._csv_file = open(self._csv_path, 'w', newline='', buffering=8192)
        self._csv_writer = csv.writer(self._csv_file)
        self._csv_writer.writerow(['timestamp', 'topic', 'expected_hz', 'actual_hz', 'status'])
        self._csv_file.flush()

    def record_topic_msg(self, topic):
        self._tracker.record(topic)

    def sample(self):
        """每 5 秒采样一次，写入 CSV，检测状态变化写入 events"""
        now = time.time()
        ts_str = datetime.fromtimestamp(now).strftime('%Y-%m-%d %H:%M:%S')

        for name, topic, expected_hz in self._topics:
            hz, status = self._tracker.get_hz_status(topic, expected_hz)
            if self._csv_writer:
                self._csv_writer.writerow([ts_str, topic, expected_hz, f'{hz:.1f}', status])

            # 检测状态变化
            prev = self._prev_status.get(topic)
            if prev is not None and prev != status:
                self._write_event(now, 'topic_status_change', {
                    'topic': topic,
                    'topic_name': name,
                    'from': prev,
                    'to': status,
                    'actual_hz': round(hz, 1),
                    'expected_hz': expected_hz,
                })
            self._prev_status[topic] = status

        if self._csv_file:
            self._csv_file.flush()
        self._sample_count += 1

    def _write_event(self, ts, event_type, data):
        """写入事件到 JSONL"""
        event = {
            'timestamp': datetime.fromtimestamp(ts).isoformat(),
            'unix': ts,
            'type': event_type,
        }
        event.update(data)
        try:
            with open(self._events_path, 'a') as f:
                f.write(json.dumps(event, ensure_ascii=False) + '\n')
        except Exception:
            pass

    def close(self):
        if self._csv_file:
            try:
                self._csv_file.flush()
                self._csv_file.close()
            except Exception:
                pass


# ============================================================================
# 节点生命周期记录器
# ============================================================================

class NodeLifecycleRecorder:
    """
    基于 rosnode 轮询，记录关键节点的上线/下线事件到 events.jsonl。
    检测短时间上线又消失的情况，标记为疑似崩溃。
    """

    def __init__(self, session_path, loc_mode=None):
        self._session_path = session_path
        self._events_path = os.path.join(session_path, 'events.jsonl')
        self._key_nodes = get_monitor_nodes(loc_mode)
        self._node_state = {}         # node_name → True/False
        self._node_first_seen = {}    # node_name → timestamp (最近一次上线时间)
        self._crash_threshold = 10.0  # 上线后 10 秒内又消失 → 疑似崩溃

    def check(self, running_nodes):
        """
        running_nodes: 当前运行的 ROS 节点名列表 (来自 rosnode.get_node_names())
        """
        now = time.time()
        running_set = set(running_nodes)

        for name in self._key_nodes:
            is_running = any(name in n for n in running_set)

            if is_running and not self._node_state.get(name, False):
                # 节点上线
                self._node_state[name] = True
                self._node_first_seen[name] = now
                self._write_event(now, 'node_online', {'node': name})

            elif not is_running and self._node_state.get(name, True):
                # 节点下线
                first_seen = self._node_first_seen.get(name)
                crash = first_seen and (now - first_seen) < self._crash_threshold
                self._node_state[name] = False
                event_data = {'node': name}
                if crash:
                    event_data['suspected_crash'] = True
                    event_data['uptime_seconds'] = round(now - first_seen, 1)
                self._write_event(now, 'node_offline', event_data)
                if crash:
                    self._write_event(now, 'node_crash_suspected', {
                        'node': name,
                        'uptime_seconds': round(now - first_seen, 1),
                        'message': f'节点 {name} 上线后仅 {now - first_seen:.1f} 秒即退出，疑似崩溃',
                    })

    def _write_event(self, ts, event_type, data):
        event = {
            'timestamp': datetime.fromtimestamp(ts).isoformat(),
            'unix': ts,
            'type': event_type,
        }
        event.update(data)
        try:
            with open(self._events_path, 'a') as f:
                f.write(json.dumps(event, ensure_ascii=False) + '\n')
        except Exception:
            pass


# ============================================================================
# 飞行日志主控制器
# ============================================================================

class FlightLogger:
    """协调所有子模块的主控制器"""

    def __init__(self):
        log_cfg = get_logging_config()
        self._storage = StorageManager(
            max_size_gb=log_cfg['max_size_gb'],
            min_free_mb=log_cfg['min_free_space_mb'],
            session_retention=log_cfg['session_retention'],
        )
        self._loc_mode = detect_localization_mode()
        self._session_id = None
        self._session_path = None
        self._start_time = None

        # 子模块 (ROS 订阅在 start 后建立)
        self._log_collector = None
        self._topic_health = None
        self._node_lifecycle = None

        # 运行状态
        self._running = False
        self._ros_node_initialized = False

        # 飞行时间追踪 (基于 /px4ctrl/state 状态转移)
        self._flight_start_time = None   # 首次进入非 MANUAL_CTRL 状态的时间
        self._flight_end_time = None     # 最后回到 MANUAL_CTRL 的时间
        self._last_px4_state = None      # 上一次的 state 值
        self._flight_active = False      # 当前是否处于飞行状态

        # 写入 PID 文件
        self._write_pid()

    def _write_pid(self):
        self._storage.ensure_logs_root()
        with open(PID_FILE, 'w') as f:
            f.write(str(os.getpid()))

    def _write_state(self, state):
        self._storage.ensure_logs_root()
        try:
            with open(STATE_FILE, 'w') as f:
                json.dump(state, f)
        except Exception:
            pass

    def start(self):
        """启动日志记录"""
        self._storage.ensure_logs_root()

        # 启动前先跑一次清理
        disk_ok, disk_warn = self._storage.check_disk_space()
        if not disk_ok:
            print(f'[flight_logger] ERROR: {disk_warn}', file=sys.stderr)
            self._write_state({'status': 'error', 'message': disk_warn})
            return False

        result = self._storage.cleanup_old_sessions(dry_run=False)
        if result.get('removed'):
            print(f'[flight_logger] 已清理 {len(result["removed"])} 个旧日志会话', file=sys.stderr)

        # 创建会话目录
        ts = time.time()
        self._start_time = ts
        ts_str = datetime.fromtimestamp(ts).strftime('%Y-%m-%d_%H-%M-%S')
        self._session_id = f'{ts_str}_{self._loc_mode}'
        self._session_path = self._storage.create_session_dir(self._session_id)

        print(f'[flight_logger] 会话开始: {self._session_id}', file=sys.stderr)
        print(f'[flight_logger] 日志目录: {self._session_path}', file=sys.stderr)

        # 写入元数据
        meta = MetadataWriter.collect_metadata(self._session_id, ts)
        MetadataWriter.write_metadata(self._session_path, meta)
        MetadataWriter.save_config_snapshot(self._session_path)

        # 初始化子模块
        self._log_collector = LogCollector(self._session_path)
        self._log_collector.open()
        self._topic_health = TopicHealthRecorder(self._session_path, self._loc_mode)
        self._topic_health.open()
        self._node_lifecycle = NodeLifecycleRecorder(self._session_path, self._loc_mode)

        # 写入初始事件
        self._write_start_event(meta)

        # 初始化 ROS
        self._init_ros()

        self._running = True
        self._write_state({
            'status': 'running',
            'session_id': self._session_id,
            'session_path': self._session_path,
            'start_time': ts,
            'pid': os.getpid(),
            'loc_mode': self._loc_mode,
        })

        # 写入导航脚本自身的终端输出记录入口
        nav_log = os.path.join(self._session_path, 'navigation_stdout.log')
        print(f'[flight_logger] navigation stdout log: {nav_log}', file=sys.stderr)

        return True

    def _write_start_event(self, meta):
        event_path = os.path.join(self._session_path, 'events.jsonl')
        event = {
            'timestamp': datetime.fromtimestamp(self._start_time).isoformat(),
            'unix': self._start_time,
            'type': 'session_start',
            'session_id': self._session_id,
            'localization_mode': self._loc_mode,
            'git_commit': meta.get('git_commit', 'unknown'),
            'git_branch': meta.get('git_branch', 'unknown'),
        }
        try:
            with open(event_path, 'a') as f:
                f.write(json.dumps(event, ensure_ascii=False) + '\n')
        except Exception:
            pass

    def _init_ros(self):
        """延迟初始化 ROS (避免在主线程阻塞)"""
        try:
            import rospy
            rospy.init_node('flight_logger', anonymous=True, disable_signals=True)
            self._ros_node_initialized = True

            # 动态发现话题类型
            tries = 0
            topic_types = {}
            while tries < 10:
                try:
                    topic_types = dict(rospy.get_published_topics())
                    if topic_types:
                        break
                except Exception:
                    pass
                rospy.sleep(1.0)
                tries += 1

            # 订阅 /rosout
            self._dynamic_sub(topic_types, '/rosout', 'rosgraph_msgs/Log',
                              self._on_rosout)

            # 订阅 /px4ctrl/state (用于记录飞行起止时间)
            self._dynamic_sub(topic_types, '/px4ctrl/state',
                              'quadrotor_msgs/Px4ctrlState', self._on_px4_state)

            # 订阅关键话题 (仅统计 Hz，不存储内容)
            for _, topic, _ in get_monitor_topics(self._loc_mode):
                rospy.Subscriber(topic, rospy.AnyMsg,
                                 self._make_hz_cb(topic))

            print(f'[flight_logger] ROS 初始化完成, 已订阅 /rosout + {len(get_monitor_topics(self._loc_mode))} 个关键话题', file=sys.stderr)

        except Exception as e:
            print(f'[flight_logger] ROS 初始化失败: {e}', file=sys.stderr)
            import traceback
            traceback.print_exc()

    def _dynamic_sub(self, topic_types, topic, default_type_str, callback):
        import rospy
        type_str = topic_types.get(topic, default_type_str)
        try:
            pkg, msg_name = type_str.split('/')
            mod = __import__(pkg + '.msg', fromlist=[msg_name])
            msg_cls = getattr(mod, msg_name)
            rospy.Subscriber(topic, msg_cls, callback)
        except Exception:
            rospy.Subscriber(topic, rospy.AnyMsg, callback)

    def _on_rosout(self, msg):
        if self._log_collector:
            self._log_collector.on_rosout_msg(msg)

    def _on_px4_state(self, msg):
        """检测飞行起止: 首次进入自主状态为起飞, 回到 MANUAL_CTRL 为降落"""
        try:
            state = getattr(msg, 'state', None)
            if state is None:
                # AnyMsg 回退: 尝试用 raw buffer 解析
                # /px4ctrl/state 类型为 quadrotor_msgs/Px4ctrlState,
                # 第一个字段是 int32 state (4 bytes little-endian)
                try:
                    state = struct.unpack_from('<i', msg._buff, 0)[0]
                except Exception:
                    return
            now = time.time()

            # 首次进入非 MANUAL_CTRL 状态 → 起飞
            if state != 1 and not self._flight_active:
                self._flight_active = True
                self._flight_start_time = now
                if self._node_lifecycle:
                    self._node_lifecycle._write_event(now, 'flight_start', {
                        'px4_state': state,
                    })

            # 从飞行状态回到 MANUAL_CTRL → 降落
            if self._flight_active and state == 1 and self._last_px4_state not in (1, None):
                self._flight_active = False
                self._flight_end_time = now
                if self._node_lifecycle:
                    self._node_lifecycle._write_event(now, 'flight_end', {
                        'px4_state': state,
                    })

            self._last_px4_state = state
        except Exception:
            pass

    def _make_hz_cb(self, topic):
        def callback(msg):
            if self._topic_health:
                self._topic_health.record_topic_msg(topic)
        return callback

    def spin_once(self):
        """单次事件循环: 采样话题健康、检查节点状态"""
        if not self._running:
            return

        now = time.time()

        # 每 5 秒采样话题健康
        last_sample = getattr(self, '_last_health_sample', 0)
        if now - last_sample >= 5.0 and self._topic_health:
            self._topic_health.sample()
            self._last_health_sample = now

        # 每 5 秒检查节点生命周期
        last_node_check = getattr(self, '_last_node_check', 0)
        if now - last_node_check >= 5.0 and self._ros_node_initialized:
            try:
                import rosnode
                running = rosnode.get_node_names()
                if self._node_lifecycle:
                    self._node_lifecycle.check(running)
            except Exception:
                pass
            self._last_node_check = now

        # 检查磁盘空间 (每 30 秒)
        last_disk_check = getattr(self, '_last_disk_check', 0)
        if now - last_disk_check >= 30.0:
            disk_ok, disk_warn = self._storage.check_disk_space()
            if not disk_ok:
                print(f'[flight_logger] WARNING: {disk_warn}', file=sys.stderr)
                self._node_lifecycle._write_event(now, 'disk_warning', {
                    'message': disk_warn,
                })
            near_limit = self._storage.warn_if_near_limit()
            if near_limit:
                print(f'[flight_logger] WARNING: {near_limit}', file=sys.stderr)
            self._last_disk_check = now

    def stop(self):
        """停止日志记录，写入结束标记"""
        if not self._running:
            return

        end_time = time.time()
        self._running = False

        try:
            print(f'[flight_logger] 停止记录, 会话: {self._session_id}', file=sys.stderr)
        except Exception:
            pass

        # 计算飞行时长
        flight_duration = None
        if self._flight_start_time:
            flight_end = self._flight_end_time if self._flight_end_time else end_time
            flight_duration = round(flight_end - self._flight_start_time, 1)

        # 写入结束事件 (含飞行时长)
        if self._node_lifecycle:
            try:
                self._node_lifecycle._write_event(end_time, 'session_stop', {
                    'session_id': self._session_id,
                    'script_duration_seconds': round(end_time - self._start_time, 1) if self._start_time else 0,
                    'flight_duration_seconds': flight_duration,
                })
            except Exception:
                pass

        # 关闭子模块 (先 flush 再 close, 确保数据落盘)
        if self._log_collector:
            try:
                self._log_collector.close()
            except Exception:
                pass
        if self._topic_health:
            try:
                self._topic_health.close()
            except Exception:
                pass

        # 最终写入元数据 (最重要的步骤)
        if self._session_path:
            try:
                MetadataWriter.finalize_metadata(
                    self._session_path, end_time,
                    flight_start_time=self._flight_start_time,
                    flight_end_time=self._flight_end_time)
            except Exception as e:
                # 最后的兜底: 直接写文件, 不依赖 yaml
                try:
                    meta_path = os.path.join(self._session_path, 'metadata.yaml')
                    with open(meta_path, 'a') as f:
                        f.write(f'end_time: "{datetime.fromtimestamp(end_time).isoformat()}"\n')
                        f.write(f'end_unix: {end_time}\n')
                        if self._start_time:
                            f.write(f'script_duration_seconds: {round(end_time - self._start_time, 1)}\n')
                        if self._flight_start_time:
                            f_end = self._flight_end_time if self._flight_end_time else end_time
                            f.write(f'flight_start_time: "{datetime.fromtimestamp(self._flight_start_time).isoformat()}"\n')
                            f.write(f'flight_end_time: "{datetime.fromtimestamp(f_end).isoformat()}"\n')
                            f.write(f'duration_seconds: {round(f_end - self._flight_start_time, 1)}\n')
                except Exception:
                    pass

        # 清理 PID/状态文件
        for f in [PID_FILE, STATE_FILE]:
            try:
                os.remove(f)
            except OSError:
                pass

        try:
            print(f'[flight_logger] 日志已归档: {self._session_path}', file=sys.stderr)
            if self._session_path:
                size = self._storage.get_dir_size(self._session_path)
                print(f'[flight_logger] 本次日志大小: {size / 1024:.1f} KB', file=sys.stderr)
        except Exception:
            pass


# ============================================================================
# 主入口
# ============================================================================

def main():
    # 信号处理必须在 logger.start() 之前注册
    # 因为 start() 内部的 _init_ros() 可能阻塞 10+ 秒连接 ROS master,
    # 如果在此期间收到 SIGHUP, 没有 handler 的话进程会被直接杀死
    _should_stop = [False]

    def handle_signal(signum, frame):
        _should_stop[0] = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGHUP, handle_signal)

    logger = FlightLogger()

    if not logger.start():
        sys.exit(1)

    try:
        while logger._running and not _should_stop[0]:
            logger.spin_once()
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f'[flight_logger] 异常: {e}', file=sys.stderr)
        import traceback
        traceback.print_exc()

    # 无论何种方式退出循环, 都在主线程正常执行 stop()
    logger.stop()


if __name__ == '__main__':
    main()