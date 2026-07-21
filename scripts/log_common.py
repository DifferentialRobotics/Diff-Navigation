#!/usr/bin/env python3
"""
Diff-Navigation 共享数据模块
monitor_dashboard.py 和 flight_logger.py 共用的话题列表、节点列表、状态判断逻辑。

避免两份代码各自维护一套 topic/node 列表导致不同步。
"""

import os
import time
from collections import deque


# ============================================================================
# 配置读取
# ============================================================================

def _get_project_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_navigation_config():
    """读取 navigation_config.yaml，失败返回空 dict"""
    try:
        import yaml
        config_path = os.path.join(_get_project_root(), 'config', 'navigation_config.yaml')
        with open(config_path, 'r') as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def detect_localization_mode():
    """返回 'lio' 或 'vio'"""
    cfg = load_navigation_config()
    mode = cfg.get('localization_mode', 'lio')
    if mode not in ('lio', 'vio'):
        return 'lio'
    return mode


def get_logging_config():
    """返回 logging 配置节，带默认值"""
    cfg = load_navigation_config()
    log_cfg = cfg.get('logging', {})
    return {
        'enabled': log_cfg.get('enabled', True),
        'max_size_gb': log_cfg.get('max_size_gb', 2.0),
        'min_free_space_mb': log_cfg.get('min_free_space_mb', 500),
        'session_retention': log_cfg.get('session_retention', 50),
    }


# ============================================================================
# 话题与节点列表 (根据定位模式自动切换)
# ============================================================================

def get_monitor_topics(loc_mode=None):
    """返回关键话题列表: [(中文名, topic, 期望Hz), ...]"""
    if loc_mode is None:
        loc_mode = detect_localization_mode()

    if loc_mode == 'vio':
        return [
            ('imu',             '/mavros/imu/data', 200.0),
            ('深度图',           '/camera/depth/image_rect_raw', 15.0),
            ('左目图像',         '/camera/infra1/image_rect_raw', 15.0),
            ('右目图像',         '/camera/infra2/image_rect_raw', 15.0),
            ('VIO里程计',        '/vins/imu_propagate', 200.0),
        ]
    else:
        return [
            ('原始点云',          '/livox/lidar', 10.0),
            ('imu',              '/mavros/imu/data', 200.0),
            ('聚类点云',          '/laserMapping/cloud_registered', 10.0),
            ('原始里程计',        '/laserMapping/odometry', 10.0),
            ('融合里程计',        '/ekf/ekf_odom', 200.0),
        ]


def get_monitor_nodes(loc_mode=None):
    """返回关键节点名列表"""
    if loc_mode is None:
        loc_mode = detect_localization_mode()

    if loc_mode == 'vio':
        return [
            'mavros', 'vins', 'px4ctrl',
            'diff_planner_node', 'multipointplan', 'traj_server',
        ]
    else:
        return [
            'mavros', 'laserMapping', 'ekf', 'px4ctrl',
            'diff_planner_node', 'multipointplan', 'traj_server',
        ]


def get_odom_topic(loc_mode=None):
    """返回里程计 topic"""
    if loc_mode is None:
        loc_mode = detect_localization_mode()
    return '/vins/imu_propagate' if loc_mode == 'vio' else '/ekf/ekf_odom'


def get_mode_display_name(loc_mode=None):
    """返回规划模式的显示名称"""
    if loc_mode is None:
        loc_mode = detect_localization_mode()
    return {'lio': '雷达', 'vio': '视觉'}.get(loc_mode, '未知')


# ============================================================================
# 话题频率计算
# ============================================================================

class TopicHzTracker:
    """轻量话题频率追踪器，3 秒滑动窗口"""

    def __init__(self, window=3.0):
        self._window = window
        self._timestamps = {}       # topic → deque of timestamps
        self._last_msg_time = {}    # topic → last message time

    def record(self, topic):
        now = time.time()
        self._last_msg_time[topic] = now
        if topic not in self._timestamps:
            self._timestamps[topic] = deque()
        ts = self._timestamps[topic]
        ts.append(now)
        cutoff = now - self._window
        while ts and ts[0] < cutoff:
            ts.popleft()

    def get_hz(self, topic):
        ts = self._timestamps.get(topic)
        if not ts:
            return 0.0
        # 修剪过期时间戳
        cutoff = time.time() - self._window
        while ts and ts[0] < cutoff:
            ts.popleft()
        return len(ts) / self._window if ts else 0.0

    def get_hz_status(self, topic, expected_hz):
        """返回 (hz, status_text)"""
        hz = self.get_hz(topic)
        if hz < 0.1:
            return hz, 'MISSING'
        if abs(hz - expected_hz) / expected_hz > 0.3:
            return hz, 'DEGRADED'
        return hz, 'OK'

    def time_since_last(self, topic):
        """距上次收到消息的秒数，未收到过返回 -1"""
        last = self._last_msg_time.get(topic)
        if last is None:
            return -1
        return time.time() - last


# ============================================================================
# 遥控器状态判断 (纯 RC 通道逻辑，无 ROS 依赖)
# ============================================================================

def parse_rc_state(channels):
    """
    根据 RC channels 数组返回 (rc_text, rc_color)
    channels: 至少 8 个元素的数组，索引对应通道号
    """
    if not channels or len(channels) < 7:
        return ('未连接', '#f38ba8')

    ch5 = channels[4] if len(channels) > 4 else 0
    ch6 = channels[5] if len(channels) > 5 else 0
    ch7 = channels[6] if len(channels) > 6 else 0

    if ch7 >= 1900:
        return ('急停', '#f38ba8')
    elif ch5 >= 1800:
        if ch6 <= 1000:
            return ('机载控制-悬停', '#a6e3a1')
        else:
            return ('机载控制-跟随轨迹', '#a6e3a1')
    elif ch5 >= 900:
        return ('手动飞行-光流', '#f9e2af')

    return ('未连接', '#f38ba8')


# ============================================================================
# 飞行状态映射 (px4ctrl/state → 显示文本)
# ============================================================================

def parse_flight_state(px4_state, vz=0.0, z=0.0):
    """
    返回 (fs_text, fs_color)
    px4_state: 1=MANUAL_CTRL, 2=AUTO_HOVER, 3=CMD_CTRL, 4=AUTO_TAKEOFF, 5=AUTO_LAND
    vz: Z 轴速度 (m/s), 负值=下降
    z: 当前 Z 坐标 (m)
    """
    if px4_state == 4:
        return ('起飞', '#a6e3a1')
    elif px4_state == 5 or (px4_state == 3 and vz < -0.3 and z < 1.0):
        return ('降落', '#a6e3a1')
    elif px4_state == 1:
        return ('手动控制', '#f38ba8')
    elif px4_state == 3:
        return ('跟随轨迹', '#a6e3a1')
    elif px4_state == 2:
        return ('悬停', '#a6e3a1')
    else:
        return (f'未知({px4_state})', '#f38ba8')


# ============================================================================
# 提示词判断
# ============================================================================

def check_sticks_centered(channels):
    """摇杆是否居中: ch0/1/3 在 1450~1550, ch2(油门) 在 1400~1600"""
    if not channels or len(channels) < 4:
        return False
    return (
        1450 <= channels[0] <= 1550 and
        1450 <= channels[1] <= 1550 and
        1400 <= channels[2] <= 1600 and
        1450 <= channels[3] <= 1550
    )


def get_hint(rc_text, fs_text, channels, px4_state):
    """根据遥控器状态和飞行状态组合返回 (提示文本, 颜色)"""
    sticks_centered = check_sticks_centered(channels)
    red = '#f38ba8'
    green = '#a6e3a1'

    # 1. 急停 + 手动控制
    if rc_text == '急停' and fs_text == '手动控制':
        return ('遥控器处于急停状态，无法自主起飞，请检查遥控器SC通道！', red)

    # 1c. 遥控器已断开
    if rc_text == '遥控器已断开':
        if fs_text == '手动控制':
            return ('遥控器已断开连接，无法自主起飞，请检查遥控器电源和连接！', red)
        else:
            return ('遥控器已断开连接，无人机可能进入失控保护，请立即检查遥控器！', red)

    # 1b. 未连接 + 手动控制
    if rc_text == '未连接' and fs_text == '手动控制':
        return ('遥控器处于未连接状态，无法自主起飞，请检查遥控器连接！', red)

    # 2. 手动模式 + 手动控制
    if rc_text == '手动飞行-光流' and fs_text == '手动控制':
        return ('遥控器处于手动状态，无法自主起飞，请检查遥控器SB通道并重启程序！', red)

    # 10. 手动模式 → 悬停
    if rc_text == '手动飞行-光流' and fs_text == '悬停':
        return ('错误操控按键，请勿起飞，请确认遥控器通道无误后重启程序！', red)

    # 3. 机载悬停 + 手动控制
    if rc_text == '机载控制-悬停' and fs_text == '手动控制':
        return ('遥控器未处于跟随轨迹状态，无法自主起飞，请检查遥控器SE通道！', red)

    # 4. 跟随轨迹 + 手动控制
    if rc_text == '机载控制-跟随轨迹' and fs_text == '手动控制':
        if sticks_centered:
            return ('状态正常，可以自主起飞！', green)
        else:
            return ('摇杆未居中，请勿自主起飞！', red)

    # 6. 跟随轨迹 + 起飞
    if rc_text == '机载控制-跟随轨迹' and fs_text == '起飞':
        return ('正在起飞！', green)

    # 7. 跟随轨迹 + 悬停/跟随轨迹
    if rc_text == '机载控制-跟随轨迹' and fs_text in ('悬停', '跟随轨迹'):
        return ('跟随轨迹状态，可按下SE进入悬停状态！', green)

    # 8. 机载悬停 + 悬停
    if rc_text == '机载控制-悬停' and fs_text == '悬停':
        return ('悬停状态，可通过遥控器摇杆操纵无人机，请勿切回跟随轨迹模式！', green)

    # 9. 跟随/悬停 + 降落
    if rc_text in ('机载控制-跟随轨迹', '机载控制-悬停') and fs_text == '降落':
        return ('正在降落！', green)

    # 11. 其余
    return ('状态异常，请确认遥控器通道无误后重启程序！严禁再次解桨！', red)


# ============================================================================
# 安全参数
# ============================================================================

def get_safety_params():
    """返回 (virtual_ceil, virtual_ground)"""
    cfg = load_navigation_config()
    planner = cfg.get('planner', {})
    return (
        planner.get('virtual_ceil', 2.0),
        planner.get('virtual_ground', 0.1),
    )
