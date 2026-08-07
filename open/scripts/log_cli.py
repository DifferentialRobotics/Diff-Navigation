#!/usr/bin/env python3
"""
Diff-Navigation 飞行日志 CLI 管理工具

用法:
  python3 scripts/log_cli.py start              # 手动启动日志记录 (后台进程)
  python3 scripts/log_cli.py stop               # 停止当前日志记录
  python3 scripts/log_cli.py status             # 查看当前记录状态
  python3 scripts/log_cli.py list               # 列出历史会话
  python3 scripts/log_cli.py show <session>     # 查看某次会话详情
  python3 scripts/log_cli.py export <session>   # 导出某次日志为 zip
  python3 scripts/log_cli.py keep <session>     # 标记保留，免于自动清除
  python3 scripts/log_cli.py unkeep <session>   # 取消保留标记
  python3 scripts/log_cli.py clean --dry-run    # 预览将被清理的旧日志
  python3 scripts/log_cli.py clean --force      # 强制执行清理
"""

import sys
import os
import time
import signal
import zipfile
import json
import yaml
import subprocess
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
LOGS_ROOT = os.path.join(PROJECT_ROOT, 'logs')
PID_FILE = os.path.join(LOGS_ROOT, '.flight_logger.pid')
STATE_FILE = os.path.join(LOGS_ROOT, '.flight_logger_state.json')

FLIGHT_LOGGER = os.path.join(SCRIPT_DIR, 'flight_logger.py')


# ============================================================================
# 工具函数
# ============================================================================

def get_logger_pid():
    try:
        with open(PID_FILE, 'r') as f:
            return int(f.read().strip())
    except Exception:
        return None


def is_logger_running():
    pid = get_logger_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def get_logger_state():
    try:
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {'status': 'stopped'}


def ensure_logs_root():
    os.makedirs(LOGS_ROOT, exist_ok=True)


# ============================================================================
# 命令实现
# ============================================================================

def cmd_start():
    """启动飞行日志后台服务"""
    if is_logger_running():
        state = get_logger_state()
        print(f"飞行日志已在运行中 (session: {state.get('session_id', 'unknown')})")
        return

    ensure_logs_root()

    # 后台启动
    print("正在启动飞行日志服务...")
    try:
        proc = subprocess.Popen(
            ['python3', FLIGHT_LOGGER],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=PROJECT_ROOT,
            start_new_session=True,
        )
        time.sleep(2)

        if is_logger_running():
            state = get_logger_state()
            print(f"飞行日志已启动")
            print(f"  Session: {state.get('session_id', 'unknown')}")
            print(f"  日志目录: {state.get('session_path', 'unknown')}")
            print(f"  PID: {state.get('pid', 'unknown')}")
        else:
            print("启动失败, 请检查日志输出")
    except Exception as e:
        print(f"启动失败: {e}")


def cmd_stop():
    """停止飞行日志后台服务"""
    if not is_logger_running():
        print("飞行日志未在运行")
        return

    pid = get_logger_pid()
    state = get_logger_state()
    session_id = state.get('session_id', 'unknown')

    print(f"正在停止飞行日志 (session: {session_id})...")
    try:
        os.kill(pid, signal.SIGTERM)
        # 等待最多 5 秒
        for _ in range(50):
            time.sleep(0.1)
            if not is_logger_running():
                break
        if is_logger_running():
            os.kill(pid, signal.SIGKILL)
            time.sleep(0.5)
        print(f"已停止 (session: {session_id})")
    except OSError as e:
        print(f"停止失败: {e}")


def cmd_status():
    """查看当前状态"""
    if is_logger_running():
        state = get_logger_state()
        print("飞行日志: 运行中")
        print(f"  Session:    {state.get('session_id', 'unknown')}")
        print(f"  日志目录:   {state.get('session_path', 'unknown')}")
        print(f"  启动时间:   {datetime.fromtimestamp(state.get('start_time', 0)).strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  定位模式:   {state.get('loc_mode', 'unknown')}")
        print(f"  PID:        {state.get('pid', 'unknown')}")

        # 统计当前日志大小
        session_path = state.get('session_path')
        if session_path and os.path.isdir(session_path):
            size = _dir_size(session_path)
            print(f"  已记录:     {size / 1024:.1f} KB")

            # 节点日志数量
            per_node = os.path.join(session_path, 'per_node')
            if os.path.isdir(per_node):
                node_files = [f for f in os.listdir(per_node) if f.endswith('.log')]
                print(f"  节点日志数: {len(node_files)} 个节点")
    else:
        print("飞行日志: 未运行")


def cmd_list():
    """列出历史日志会话"""
    if not os.path.isdir(LOGS_ROOT):
        print("暂无日志记录")
        return

    sessions = []
    for name in sorted(os.listdir(LOGS_ROOT)):
        if name.startswith('.'):
            continue
        path = os.path.join(LOGS_ROOT, name)
        if not os.path.isdir(path):
            continue
        meta_path = os.path.join(path, 'metadata.yaml')
        meta = {}
        if os.path.exists(meta_path):
            try:
                with open(meta_path, 'r') as f:
                    meta = yaml.safe_load(f) or {}
            except Exception:
                pass

        size = _dir_size(path)
        kept = meta.get('keep', False)
        sessions.append({
            'name': name,
            'path': path,
            'size': size,
            'kept': kept,
            'meta': meta,
        })

    if not sessions:
        print("暂无日志记录")
        return

    # 按时间排序 (新→旧)
    sessions.sort(key=lambda s: s['name'], reverse=True)

    print(f"{'Session':<35} {'大小':>10} {'飞行时长':>8} {'脚本时长':>8} {'模式':>6} {'保留':>4}")
    print("-" * 80)
    for s in sessions:
        m = s['meta']
        flight_dur = m.get('duration_seconds')
        script_dur = m.get('script_duration_seconds')
        flight_str = _fmt_duration(flight_dur) if flight_dur else '--'
        script_str = _fmt_duration(script_dur) if script_dur else '--'
        mode = m.get('localization_mode', '--')
        kept_str = '★' if s['kept'] else ''
        print(f'{s["name"]:<35} {_fmt_size(s["size"]):>10} {flight_str:>8} {script_str:>8} {mode:>6} {kept_str:>4}')

    # 汇总
    total_size = sum(s['size'] for s in sessions)
    kept_count = sum(1 for s in sessions if s['kept'])
    print("-" * 70)
    print(f'共 {len(sessions)} 次飞行, 总大小 {_fmt_size(total_size)}, {kept_count} 个已标记保留')


def cmd_show(session_name):
    """查看会话详情"""
    path = _find_session(session_name)
    if not path:
        return

    meta_path = os.path.join(path, 'metadata.yaml')
    if os.path.exists(meta_path):
        print("=== 元数据 ===")
        with open(meta_path, 'r') as f:
            print(f.read())

    events_path = os.path.join(path, 'events.jsonl')
    if os.path.exists(events_path):
        print("=== 关键事件 ===")
        with open(events_path, 'r') as f:
            for line in f:
                try:
                    evt = json.loads(line)
                    ts = evt.get('timestamp', '')[:19]
                    etype = evt.get('type', '')
                    if etype in ('session_start', 'session_stop'):
                        script_dur = evt.get('script_duration_seconds', '')
                        flight_dur = evt.get('flight_duration_seconds', '')
                        extra = f' 脚本{_fmt_duration(script_dur)}' if script_dur else ''
                        extra += f' 飞行{_fmt_duration(flight_dur)}' if flight_dur else ''
                        print(f'  [{ts}] {etype}{extra}')
                    elif etype == 'flight_start':
                        print(f'  [{ts}] 起飞 (px4_state={evt.get("px4_state","?")})')
                    elif etype == 'flight_end':
                        print(f'  [{ts}] 降落 (px4_state={evt.get("px4_state","?")})')
                    elif etype == 'node_online':
                        print(f'  [{ts}] 节点上线: {evt.get("node","?")}')
                    elif etype == 'node_offline':
                        crash = ' (疑似崩溃)' if evt.get('suspected_crash') else ''
                        print(f'  [{ts}] 节点下线: {evt.get("node","?")}{crash}')
                    elif etype == 'topic_status_change':
                        print(f'  [{ts}] 话题状态变化: {evt.get("topic_name","?")} ({evt.get("topic","?")}) {evt.get("from","?")}→{evt.get("to","?")} Hz={evt.get("actual_hz","?")}')
                    elif etype == 'disk_warning':
                        print(f'  [{ts}] 磁盘告警: {evt.get("message","?")}')
                    else:
                        print(f'  [{ts}] {etype} {json.dumps(evt, ensure_ascii=False)}')
                except Exception:
                    pass

    # 节点日志统计
    per_node = os.path.join(path, 'per_node')
    if os.path.isdir(per_node):
        print("\n=== 节点日志 ===")
        for f in sorted(os.listdir(per_node)):
            if f.endswith('.log'):
                fp = os.path.join(per_node, f)
                lines = _count_lines(fp)
                size = os.path.getsize(fp)
                print(f'  {f:<40} {lines:>6} 行  {_fmt_size(size)}')

    # Topic 健康
    health_csv = os.path.join(path, 'topic_health.csv')
    if os.path.exists(health_csv):
        print("\n=== 话题健康 (最近 5 条) ===")
        with open(health_csv, 'r') as f:
            lines = f.readlines()
        print(f'  (共 {len(lines)-1} 条记录)')
        for line in lines[-5:]:
            print(f'  {line.strip()}')


def cmd_export(session_name):
    """导出日志为 zip 文件"""
    path = _find_session(session_name)
    if not path:
        return

    zip_name = f'{os.path.basename(path)}.zip'
    zip_path = os.path.join(os.path.dirname(path) if os.path.dirname(path) else PROJECT_ROOT, zip_name)

    print(f'正在导出 {os.path.basename(path)} → {zip_name}...')

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for dirpath, _, filenames in os.walk(path):
            arc_dir = os.path.relpath(dirpath, os.path.dirname(path))
            for f in filenames:
                fp = os.path.join(dirpath, f)
                arc_name = os.path.join(arc_dir, f)
                zf.write(fp, arc_name)

    zip_size = os.path.getsize(zip_path)
    print(f'导出完成: {zip_path} ({_fmt_size(zip_size)})')
    print(f'可提供给研发/售后分析使用')


def cmd_keep(session_name):
    """标记会话为保留"""
    _toggle_keep(session_name, True)


def cmd_unkeep(session_name):
    """取消保留标记"""
    _toggle_keep(session_name, False)


def _toggle_keep(session_name, keep_val):
    path = _find_session(session_name)
    if not path:
        return
    meta_path = os.path.join(path, 'metadata.yaml')
    meta = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path, 'r') as f:
                meta = yaml.safe_load(f) or {}
        except Exception:
            pass
    meta['keep'] = keep_val
    with open(meta_path, 'w') as f:
        yaml.dump(meta, f, default_flow_style=False, allow_unicode=True)
    print(f'已{"标记保留" if keep_val else "取消保留"}: {os.path.basename(path)}')


def cmd_clean(dry_run=True):
    """清理旧日志"""
    from log_common import get_logging_config
    log_cfg = get_logging_config()

    # 导入 StorageManager
    sys.path.insert(0, SCRIPT_DIR)
    from flight_logger import StorageManager

    storage = StorageManager(
        max_size_gb=log_cfg['max_size_gb'],
        min_free_mb=log_cfg['min_free_space_mb'],
        session_retention=log_cfg['session_retention'],
    )

    if dry_run:
        print("=== 清理预览 (--dry-run) ===")
        result = storage.cleanup_old_sessions(dry_run=True)
        if result['removed']:
            print(f"将删除 {len(result['removed'])} 个旧会话:")
            for s in result['removed']:
                print(f'  - {s}')
        else:
            print("无需清理")
        print(f"剩余: {result['remaining_total']} 个会话 (含 {len(result['remaining_kept'])} 个保留)")
    else:
        result = storage.cleanup_old_sessions(dry_run=False)
        if result['removed']:
            print(f"已删除 {len(result['removed'])} 个旧会话")
        else:
            print("无需清理")
        print(f"总大小: {result['total_size_gb']:.2f} GB")


# ============================================================================
# 辅助函数
# ============================================================================

def _find_session(session_name):
    """模糊匹配会话名，返回完整路径"""
    if not os.path.isdir(LOGS_ROOT):
        print(f'日志目录不存在: {LOGS_ROOT}')
        return None

    # 前缀匹配
    matches = []
    for name in os.listdir(LOGS_ROOT):
        if name.startswith(session_name) and os.path.isdir(os.path.join(LOGS_ROOT, name)):
            matches.append(name)

    if len(matches) == 1:
        return os.path.join(LOGS_ROOT, matches[0])
    elif len(matches) > 1:
        print(f'匹配到多个会话，请更精确指定:')
        matches.sort(reverse=True)
        for m in matches[:10]:
            print(f'  {m}')
        return None
    else:
        # 列出最近的会话供参考
        print(f'未找到会话: {session_name}')
        all_sessions = sorted(
            [n for n in os.listdir(LOGS_ROOT) if not n.startswith('.') and os.path.isdir(os.path.join(LOGS_ROOT, n))],
            reverse=True
        )
        if all_sessions:
            print(f'\n最近会话:')
            for s in all_sessions[:5]:
                print(f'  {s}')
        return None


def _dir_size(path):
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


def _count_lines(path):
    try:
        with open(path, 'r') as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def _fmt_duration(seconds):
    if seconds is None:
        return '--'
    if seconds < 60:
        return f'{seconds:.0f}s'
    elif seconds < 3600:
        return f'{seconds / 60:.1f}m'
    else:
        h = int(seconds / 3600)
        m = int((seconds % 3600) / 60)
        return f'{h}h{m}m'


def _fmt_size(size_bytes):
    if size_bytes < 1024:
        return f'{size_bytes}B'
    elif size_bytes < 1024 * 1024:
        return f'{size_bytes / 1024:.1f}K'
    elif size_bytes < 1024 * 1024 * 1024:
        return f'{size_bytes / 1024 / 1024:.1f}M'
    else:
        return f'{size_bytes / 1024 / 1024 / 1024:.2f}G'


# ============================================================================
# 入口
# ============================================================================

def print_usage():
    print(__doc__)


def main():
    if len(sys.argv) < 2:
        print_usage()
        return

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd == 'start':
        cmd_start()
    elif cmd == 'stop':
        cmd_stop()
    elif cmd == 'status':
        cmd_status()
    elif cmd == 'list':
        cmd_list()
    elif cmd == 'show':
        if not args:
            print("用法: python3 scripts/log_cli.py show <session>")
            return
        cmd_show(args[0])
    elif cmd == 'export':
        if not args:
            print("用法: python3 scripts/log_cli.py export <session>")
            return
        cmd_export(args[0])
    elif cmd == 'keep':
        if not args:
            print("用法: python3 scripts/log_cli.py keep <session>")
            return
        cmd_keep(args[0])
    elif cmd == 'unkeep':
        if not args:
            print("用法: python3 scripts/log_cli.py unkeep <session>")
            return
        cmd_unkeep(args[0])
    elif cmd == 'clean':
        if '--force' in args:
            cmd_clean(dry_run=False)
        else:
            cmd_clean(dry_run=True)
    elif cmd in ('-h', '--help', 'help'):
        print_usage()
    else:
        print(f"未知命令: {cmd}")
        print_usage()


if __name__ == '__main__':
    main()
