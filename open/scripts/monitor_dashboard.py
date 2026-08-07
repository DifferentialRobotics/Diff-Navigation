#!/usr/bin/env python3
"""
Diff-Navigation 监控面板 (PyQt5)
实时显示节点状态、话题频率、关键状态、位置信息

依赖: sudo apt install python3-pyqt5
"""

import sys
import os
import time
import math
import traceback
from collections import deque

# --- PyQt5 ---
from PyQt5.QtCore import (
    Qt, QThread, QObject, pyqtSignal, QTimer
)
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QLabel, QProgressBar, QTextEdit, QGridLayout, QSplitter,
)
from PyQt5.QtGui import QFont, QColor, QPalette, QTextCursor

# --- ROS imports ---
import rospy
import rosnode

# --- 共享数据 (与 flight_logger.py 共用, 避免重复维护) ---
from log_common import (
    detect_localization_mode, get_safety_params,
    get_monitor_topics, get_monitor_nodes, get_odom_topic,
    get_mode_display_name,
    parse_rc_state, parse_flight_state,
    check_sticks_centered, get_hint,
)

LOC_MODE = detect_localization_mode()
VIRTUAL_CEIL, VIRTUAL_GROUND = get_safety_params()
ODOM_TOPIC = get_odom_topic(LOC_MODE)
MONITOR_TOPICS = get_monitor_topics(LOC_MODE)
MONITOR_NODES = get_monitor_nodes(LOC_MODE)


# ============================================================================
# 暗色主题 QSS 样式
# ============================================================================
DARK_THEME = """
QMainWindow { background-color: #1e1e2e; color: #cdd6f4; }
QGroupBox {
    border: 1px solid #45475a; border-radius: 6px; margin-top: 12px;
    padding-top: 8px; font-weight: bold; font-size: 13px; color: #89b4fa;
}
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: #89b4fa; }
QTableWidget {
    background-color: #181825; alternate-background-color: #1e1e2e;
    gridline-color: #313244; border: 1px solid #45475a; border-radius: 4px;
    color: #cdd6f4; font-size: 12px;
}
QTableWidget::item { padding: 2px 6px; }
QHeaderView::section {
    background-color: #313244; color: #a6adc8; border: none;
    padding: 4px 8px; font-size: 11px; font-weight: bold;
}
QLabel { color: #cdd6f4; font-size: 13px; }
QProgressBar {
    border: 1px solid #45475a; border-radius: 4px; background-color: #181825;
    text-align: center; color: #cdd6f4; font-size: 12px;
}
QProgressBar::chunk { background-color: #a6e3a1; border-radius: 3px; }
QTextEdit {
    background-color: #181825; border: 1px solid #45475a; border-radius: 4px;
    color: #a6adc8; font-size: 12px; font-family: "Monospace";
}
"""


# ============================================================================
# ROS 数据采集器 (QThread 子类，在独立线程中 spin)
# ============================================================================
class ROSWorker(QThread):
    """在独立线程中运行 ROS: init_node → 创建订阅 → spin"""

    battery_signal = pyqtSignal(object)
    rc_signal = pyqtSignal(object)
    mavros_state_signal = pyqtSignal(object)
    px4_state_signal = pyqtSignal(object)
    odom_signal = pyqtSignal(object)
    goal_signal = pyqtSignal(object)
    topic_hz_signal = pyqtSignal(str, float)
    log_signal = pyqtSignal(str)
    planner_log_signal = pyqtSignal(str, int)

    def __init__(self):
        super().__init__()
        self._topic_timestamps = {}
        self._topic_last_msg_time = {}
        self._hz_window = 3.0

    def run(self):
        """QThread 入口: init ROS → 动态发现消息类型 → subscribe → spin"""

        # ---- 1. 初始化 ROS 节点 ----
        try:
            rospy.init_node('monitor_dashboard', anonymous=True, disable_signals=True)
        except Exception as e:
            print(f"[MONITOR ERROR] rospy.init_node failed: {e}", file=sys.stderr)
            self.log_signal.emit(f"ERROR: rospy.init_node failed: {e}")
            return

        self.log_signal.emit("ROS 节点 OK, 正在发现话题类型...")

        # ---- 2. 等待 ROS master 中话题信息就绪, 然后动态获取类型 ----
        tries = 0
        topic_types = {}
        while tries < 10:
            try:
                topics = rospy.get_published_topics()
                topic_types = dict(topics)
                if topic_types:
                    break
            except Exception:
                pass
            rospy.sleep(1.0)
            tries += 1

        # 打印发现的关键话题类型 (调试用)
        for t in ['/mavros/battery', '/mavros/rc/in', '/mavros/state',
                   '/px4ctrl/state', '/ekf/ekf_odom', '/goal']:
            actual = topic_types.get(t, '未发现')
            print(f"[MONITOR] 话题 {t} → 类型: {actual}", file=sys.stderr)

        # ---- 3. 动态创建订阅 (用话题实际的消息类型) ----
        self._dynamic_sub(topic_types, '/mavros/battery',
                          'sensor_msgs/BatteryState', self._battery_cb)
        self._dynamic_sub(topic_types, '/mavros/rc/in',
                          'mavros_msgs/RCIn', self._rc_cb)
        self._dynamic_sub(topic_types, '/mavros/state',
                          'mavros_msgs/State', self._mavros_state_cb)
        self._dynamic_sub(topic_types, '/px4ctrl/state',
                          'quadrotor_msgs/Px4ctrlState', self._px4_state_cb)
        self._dynamic_sub(topic_types, ODOM_TOPIC,
                          'nav_msgs/Odometry', self._odom_cb)
        self._dynamic_sub(topic_types, '/goal',
                          'geometry_msgs/PoseStamped', self._goal_cb)
        # 规划器日志 (rosout 中过滤 diff_planner / traj_server 的消息)
        self._dynamic_sub(topic_types, '/rosout',
                          'rosgraph_msgs/Log', self._rosout_cb)

        # 话题频率监测 (根据定位模式自动选择话题列表)
        for _, topic, _ in MONITOR_TOPICS:
            rospy.Subscriber(topic, rospy.AnyMsg, self._make_hz_cb(topic))

        self.log_signal.emit("话题订阅完成, 开始接收数据...")

        # ---- 4. spin ----
        try:
            rospy.spin()
        except Exception as e:
            print(f"[MONITOR ERROR] rospy.spin 异常退出: {e}", file=sys.stderr)
        self.log_signal.emit("ROS 线程退出")

    def _dynamic_sub(self, topic_types, topic, default_type_str, callback):
        """用话题实际类型创建订阅, 失败则回退到 AnyMsg (兼容一切)"""
        type_str = topic_types.get(topic, default_type_str)
        try:
            # 从字符串 "pkg/MsgType" 动态构造 Message class
            pkg, msg_name = type_str.split('/')
            mod = __import__(pkg + '.msg', fromlist=[msg_name])
            msg_cls = getattr(mod, msg_name)
            rospy.Subscriber(topic, msg_cls, callback)
            print(f"[MONITOR] 订阅 {topic} → {type_str} OK", file=sys.stderr)
        except Exception as e:
            print(f"[MONITOR] 订阅 {topic} 类型 {type_str} 失败, 改用 AnyMsg: {e}", file=sys.stderr)
            rospy.Subscriber(topic, rospy.AnyMsg, callback)

    # ---- 带容错包裹的回调 (首次收到数据打印确认) ----
    _first_battery = True
    _first_rc = True
    _first_mavros = True
    _first_px4 = True
    _first_odom = True
    _first_goal = True

    def _battery_cb(self, msg):
        try:
            if self._first_battery:
                print("[MONITOR] 首次收到 /mavros/battery 数据 OK", file=sys.stderr)
                self._first_battery = False
            self.battery_signal.emit(msg)
        except Exception as e:
            print(f"[MONITOR] battery_cb error: {e}", file=sys.stderr)
            traceback.print_exc()

    def _rc_cb(self, msg):
        try:
            if self._first_rc:
                print("[MONITOR] 首次收到 /mavros/rc/in 数据 OK", file=sys.stderr)
                self._first_rc = False
            self._topic_last_msg_time['/mavros/rc/in'] = time.time()
            self.rc_signal.emit(msg)
        except Exception as e:
            print(f"[MONITOR] rc_cb error: {e}", file=sys.stderr)
            traceback.print_exc()

    def _mavros_state_cb(self, msg):
        try:
            if self._first_mavros:
                print("[MONITOR] 首次收到 /mavros/state 数据 OK", file=sys.stderr)
                self._first_mavros = False
            self.mavros_state_signal.emit(msg)
        except Exception as e:
            print(f"[MONITOR] mavros_state_cb error: {e}", file=sys.stderr)
            traceback.print_exc()

    def _px4_state_cb(self, msg):
        try:
            if self._first_px4:
                print("[MONITOR] 首次收到 /px4ctrl/state 数据 OK", file=sys.stderr)
                self._first_px4 = False
            self.px4_state_signal.emit(msg)
        except Exception as e:
            print(f"[MONITOR] px4_state_cb error: {e}", file=sys.stderr)
            traceback.print_exc()

    def _odom_cb(self, msg):
        try:
            if self._first_odom:
                print("[MONITOR] 首次收到 /ekf/ekf_odom 数据 OK", file=sys.stderr)
                self._first_odom = False
            self.odom_signal.emit(msg)
        except Exception as e:
            print(f"[MONITOR] odom_cb error: {e}", file=sys.stderr)
            traceback.print_exc()

    def _goal_cb(self, msg):
        try:
            if self._first_goal:
                print("[MONITOR] 首次收到 /goal 数据 OK", file=sys.stderr)
                self._first_goal = False
            self.goal_signal.emit(msg)
        except Exception as e:
            print(f"[MONITOR] goal_cb error: {e}", file=sys.stderr)
            traceback.print_exc()

    def _rosout_cb(self, msg):
        """接收 /rosout 全部日志, 加上 [节点名] 前缀"""
        try:
            name = msg.name if hasattr(msg, 'name') else 'unknown'
            text = msg.msg if hasattr(msg, 'msg') else str(msg)
            level = msg.level if hasattr(msg, 'level') else 2
            self.planner_log_signal.emit(f"[{name}] {text}", level)
        except Exception:
            pass

    def _make_hz_cb(self, topic_name):
        def callback(msg):
            now = time.time()
            self._topic_last_msg_time[topic_name] = now
            if topic_name not in self._topic_timestamps:
                self._topic_timestamps[topic_name] = deque()
            timestamps = self._topic_timestamps[topic_name]
            timestamps.append(now)
            cutoff = now - self._hz_window
            while timestamps and timestamps[0] < cutoff:
                timestamps.popleft()
            hz = len(timestamps) / self._hz_window if timestamps else 0.0
            self.topic_hz_signal.emit(topic_name, hz)
        return callback


# ============================================================================
# 节点存活检查器
# ============================================================================
class NodeMonitor(QObject):
    """在主线程用 QTimer 定期检查 ROS 节点存活"""

    nodes_signal = pyqtSignal(dict)
    log_signal = pyqtSignal(str)

    KEY_NODES = MONITOR_NODES

    def __init__(self):
        super().__init__()
        self._running_nodes_cache = set()

    def start_monitor(self):
        self._timer = QTimer()
        self._timer.timeout.connect(self._check_nodes)
        self._timer.start(3000)
        self._check_nodes()

    def _check_nodes(self):
        try:
            running = rosnode.get_node_names()
            self._running_nodes_cache = set(running)
        except Exception as e:
            self.log_signal.emit(f"节点检查失败: {e}")
            running = []

        status = {}
        for name in self.KEY_NODES:
            status[name] = any(name in n for n in running)
        self.nodes_signal.emit(status)


# ============================================================================
# 主窗口
# ============================================================================
class MonitorWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Diff-Navigation Monitor")
        self.resize(960, 780)
        self.setMinimumSize(860, 680)
        self.setStyleSheet(DARK_THEME)

        self._current_pos = (0.0, 0.0, 0.0)
        self._last_z = 0.0
        self._last_z_time = 0.0
        self._vz = 0.0  # Z轴速度 (m/s), 负=下降
        self._goal_pos = None
        self._topic_hz = {}
        self._hint_locked = False  # 手动控制→悬停后锁定提示
        self._locked_hint_text = ""
        self._last_odom_time = 0
        self._goal_z_safe = True
        self._goal_safety_msg = ""

        self._setup_ui()
        self._setup_threads()

    # ------------------------------------------------------------------
    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # ---- 左侧 ----
        left = QVBoxLayout()

        # 规划模式
        mode_text = get_mode_display_name(LOC_MODE)
        mode_colors = {'雷达': '#89b4fa', '视觉': '#a6e3a1'}
        mode_group = QGroupBox("定位模式")
        mode_layout = QHBoxLayout()
        mode_label = QLabel(mode_text)
        mode_label.setFont(QFont("Monospace", 16, QFont.Bold))
        mode_label.setStyleSheet(f"color: {mode_colors.get(mode_text, '#cdd6f4')};")
        mode_label.setAlignment(Qt.AlignCenter)
        mode_layout.addWidget(mode_label)
        mode_group.setLayout(mode_layout)
        left.addWidget(mode_group)

        self._node_table = self._make_table(["节点名称", "状态"], len(NodeMonitor.KEY_NODES))
        for i, name in enumerate(NodeMonitor.KEY_NODES):
            self._node_table.setItem(i, 0, QTableWidgetItem(name))
            item = QTableWidgetItem("--")
            item.setTextAlignment(Qt.AlignCenter)
            self._node_table.setItem(i, 1, item)
        g = QGroupBox("节点状态")
        g.setLayout(self._wrap(self._node_table))
        left.addWidget(g)

        self._info_group = QGroupBox("无人机信息")
        grid = QGridLayout()
        grid.addWidget(QLabel("当前位置:"), 0, 0)
        self._pos_label = QLabel("x:  --   y:  --   z:  --")
        self._pos_label.setFont(QFont("Monospace", 12))
        self._pos_label.setStyleSheet("color: #a6e3a1;")
        grid.addWidget(self._pos_label, 0, 1)
        grid.addWidget(QLabel("目标位置:"), 1, 0)
        self._goal_label = QLabel("x:  --   y:  --   z:  --")
        self._goal_label.setFont(QFont("Monospace", 11))
        self._goal_label.setStyleSheet("color: #fab387;")
        grid.addWidget(self._goal_label, 1, 1)
        grid.addWidget(QLabel("距离:"), 2, 0)
        self._dist_label = QLabel("--")
        self._dist_label.setFont(QFont("Monospace", 14, QFont.Bold))
        self._dist_label.setStyleSheet("color: #f5c2e7;")
        grid.addWidget(self._dist_label, 2, 1)
        self._info_group.setLayout(grid)
        left.addWidget(self._info_group)

        # ---- 右侧 ----
        right = QVBoxLayout()

        self._monitor_topics = MONITOR_TOPICS
        self._topic_table = self._make_table(["话题", "话题名", "频率", "状态"], len(self._monitor_topics))
        self._topic_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self._topic_table.setColumnWidth(0, 70)
        self._topic_table.setColumnWidth(1, 195)
        self._topic_table.setColumnWidth(2, 55)
        for i, (name, topic, _) in enumerate(self._monitor_topics):
            self._topic_table.setItem(i, 0, QTableWidgetItem(name))
            self._topic_table.setItem(i, 1, QTableWidgetItem(topic))
            item_hz = QTableWidgetItem("-- Hz")
            item_hz.setTextAlignment(Qt.AlignCenter)
            self._topic_table.setItem(i, 2, item_hz)
            item_st = QTableWidgetItem("未连接")
            item_st.setTextAlignment(Qt.AlignCenter)
            item_st.setForeground(QColor("#f38ba8"))
            self._topic_table.setItem(i, 3, item_st)
        g2 = QGroupBox("关键话题")
        g2.setLayout(self._wrap(self._topic_table))
        right.addWidget(g2)

        self._state_group = QGroupBox("关键状态")
        sl = QVBoxLayout()
        bl = QHBoxLayout()
        bl.addWidget(QLabel("电量:"))
        self._battery_bar = QProgressBar()
        self._battery_bar.setRange(0, 100)
        bl.addWidget(self._battery_bar, 1)
        sl.addLayout(bl)
        self._battery_detail = QLabel("等待数据...")
        self._battery_detail.setFont(QFont("Monospace", 11))
        sl.addWidget(self._battery_detail)

        rc_layout = QHBoxLayout()
        rc_layout.addWidget(QLabel("遥控器:"))
        self._rc_label = QLabel("等待数据...")
        self._rc_label.setFont(QFont("Monospace", 11))
        self._rc_label.setStyleSheet("color: #89b4fa;")
        rc_layout.addWidget(self._rc_label)
        rc_layout.addStretch()
        sl.addLayout(rc_layout)

        fs_layout = QHBoxLayout()
        fs_layout.addWidget(QLabel("飞行状态:"))
        self._fs_label = QLabel("等待数据...")
        self._fs_label.setFont(QFont("Monospace", 11))
        self._fs_label.setStyleSheet("color: #89b4fa;")
        fs_layout.addWidget(self._fs_label)
        fs_layout.addStretch()
        sl.addLayout(fs_layout)

        # 提示
        hint_layout = QHBoxLayout()
        hint_layout.addWidget(QLabel("提示:"))
        self._hint_label = QLabel("等待数据...")
        self._hint_label.setFont(QFont("Monospace", 10))
        self._hint_label.setStyleSheet("color: #f9e2af;")
        self._hint_label.setWordWrap(True)
        hint_layout.addWidget(self._hint_label, 1)
        sl.addLayout(hint_layout)

        self._state_group.setLayout(sl)
        right.addWidget(self._state_group)

        # 日志
        log_group = QGroupBox("日志")
        self._log_area = QTextEdit()
        self._log_area.setReadOnly(True)
        self._log_area.setMinimumHeight(130)
        log_group.setLayout(self._wrap(self._log_area))

        # 组合
        lw = QWidget()
        lw.setLayout(left)
        lw.setMaximumWidth(340)
        rw = QWidget()
        right_side = QVBoxLayout()
        right_side.addLayout(right)
        right_side.addWidget(log_group)
        rw.setLayout(right_side)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(lw)
        splitter.addWidget(rw)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        main_layout.addWidget(splitter)

    def _make_table(self, headers, rows):
        t = QTableWidget()
        t.setColumnCount(len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.horizontalHeader().setStretchLastSection(True)
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        t.setRowCount(rows)
        t.setAlternatingRowColors(True)
        t.verticalHeader().setVisible(False)
        t.setSelectionMode(QTableWidget.NoSelection)
        return t

    def _wrap(self, widget):
        l = QVBoxLayout()
        l.addWidget(widget)
        return l

    # ------------------------------------------------------------------
    def _setup_threads(self):
        # ROS 线程 (QThread 子类，自带 run/spin)
        self._ros_worker = ROSWorker()
        self._ros_worker.battery_signal.connect(self._on_battery)
        self._ros_worker.rc_signal.connect(self._on_rc)
        self._ros_worker.mavros_state_signal.connect(self._on_mavros_state)
        self._ros_worker.px4_state_signal.connect(self._on_px4_state)
        self._ros_worker.odom_signal.connect(self._on_odom)
        self._ros_worker.goal_signal.connect(self._on_goal)
        self._ros_worker.topic_hz_signal.connect(self._on_topic_hz)
        self._ros_worker.log_signal.connect(self._log_event)
        self._ros_worker.planner_log_signal.connect(self._planner_log)
        self._ros_worker.start()  # 启动 QThread → 调用 run()

        # 节点检查器 (主线程 QTimer)
        self._node_monitor = NodeMonitor()
        self._node_monitor.nodes_signal.connect(self._on_nodes)
        self._node_monitor.log_signal.connect(self._log_event)
        self._node_monitor.start_monitor()

        # 话题超时检测 (主线程 QTimer, 1.5秒)
        self._topic_timeout_timer = QTimer()
        self._topic_timeout_timer.timeout.connect(self._check_topic_timeout)
        self._topic_timeout_timer.start(1500)

        self._log_event("监控面板已启动，等待 ROS 连接...")

    # ==================================================================
    # 回调处理
    # ==================================================================

    def _on_battery(self, msg):
        try:
            voltage = msg.voltage
            if voltage <= 0:
                return
            # 满电 25.2V(100%), 空电 19.2V(0%), 满电飞行时间 18min
            percent = (voltage - 19.2) / (25.2 - 19.2) * 100.0
            percent = max(0.0, min(100.0, percent))
            remaining = percent / 100.0 * 18.0

            self._battery_bar.setValue(int(percent))
            time_color = "#f38ba8" if remaining < 5 else "#a6e3a1"
            self._battery_detail.setText(
                f"电压：{voltage:.1f}V  ({percent:.0f}%)  预计剩余飞行时间: "
                f"<span style='color:{time_color};font-weight:bold;'>{remaining:.0f} min</span>")
            if percent < 20:
                self._battery_bar.setStyleSheet("QProgressBar::chunk { background-color: #f38ba8; border-radius: 3px; }")
            else:
                self._battery_bar.setStyleSheet("QProgressBar::chunk { background-color: #a6e3a1; border-radius: 3px; }")
        except Exception as e:
            print(f"[MONITOR] _on_battery error: {e}", file=sys.stderr)
            traceback.print_exc()

    def _on_rc(self, msg):
        try:
            ch = None
            if hasattr(msg, 'channels'):
                ch = msg.channels
            elif hasattr(msg, '_buff'):
                import struct
                buf = msg._buff
                if len(buf) > 12:
                    off = 4 + 8 + 4
                    frame_len = struct.unpack_from('<I', buf, off)[0]
                    off += 4 + frame_len
                    off += 1
                    if off + 4 <= len(buf):
                        n = struct.unpack_from('<I', buf, off)[0]
                        off += 4
                        ch = []
                        for _ in range(min(n, 18)):
                            ch.append(struct.unpack_from('<H', buf, off)[0])
                            off += 2
            if ch:
                self._last_rc_ch = ch
            self._update_rc_fs_hint()
        except Exception as e:
            print(f"[MONITOR] _on_rc error: {e}", file=sys.stderr)

    def _on_mavros_state(self, msg):
        try:
            status = None
            if hasattr(msg, 'system_status'):
                status = msg.system_status
            elif hasattr(msg, '_buff'):
                try:
                    from mavros_msgs.msg import State
                    status = State().deserialize(msg._buff).system_status
                except Exception:
                    pass
            if status is not None:
                self._armed = (status == 8)
            self._update_rc_fs_hint()
        except Exception as e:
            print(f"[MONITOR] _on_mavros_state error: {e}", file=sys.stderr)

    def _on_px4_state(self, msg):
        try:
            self._prev_px4_state = getattr(self, '_last_px4_state', None)
            self._last_px4_state = msg.state
            self._update_rc_fs_hint()
        except Exception as e:
            print(f"[MONITOR] _on_px4_state error: {e}", file=sys.stderr)

    def _update_rc_fs_hint(self):
        """统一更新遥控器状态、飞行状态、提示词 (优先级: 健康 > 锁定 > 正常, 目标安全追加到末尾)"""
        # 1. 健康检查 (最高优先级, 覆盖一切)
        if self._apply_health_hint():
            return

        # 2. 锁定提示
        if self._hint_locked:
            hint = self._locked_hint_text
            if not self._goal_z_safe:
                hint = f"{hint} {self._goal_safety_msg}"
            self._hint_label.setText(hint)
            self._hint_label.setStyleSheet("color: #f38ba8; font-size: 14px; font-weight: bold;")
            return

        armed = getattr(self, '_armed', False)
        ch = getattr(self, '_last_rc_ch', None)
        s = getattr(self, '_last_px4_state', 1)
        z = self._current_pos[2] if self._current_pos else 0.0

        # ---- 遥控器状态 (RC channels) ----
        # RC 超时检测: 2秒内未收到 /mavros/rc/in 消息, 判定遥控器断开
        if getattr(self, '_rc_timeout', False):
            rc_text, rc_color = "遥控器已断开", "#f38ba8"
        elif ch:
            rc_text, rc_color = parse_rc_state(ch)
        else:
            rc_text, rc_color = "未连接", "#f38ba8"

        self._rc_label.setText(rc_text)
        self._rc_label.setStyleSheet(f"color: {rc_color}; font-family: Monospace; font-size: 14px; font-weight: bold;")

        # ---- 飞行状态 (px4ctrl/state) ----
        fs_text, fs_color = parse_flight_state(s, self._vz, z)

        self._fs_label.setText(fs_text)
        self._fs_label.setStyleSheet(f"color: {fs_color}; font-family: Monospace; font-size: 14px; font-weight: bold;")

        # ---- 检测非法跃迁: 手动控制(1) → 悬停(2) ----
        prev_s = getattr(self, '_prev_px4_state', None)
        if prev_s == 1 and s == 2:
            self._hint_locked = True
            self._locked_hint_text = "错误操控按键，请勿起飞，请确认遥控器通道无误后重启程序！"
            hint = self._locked_hint_text
            if not self._goal_z_safe:
                hint = f"{hint} {self._goal_safety_msg}"
            self._hint_label.setText(hint)
            self._hint_label.setStyleSheet("color: #f38ba8; font-size: 14px; font-weight: bold;")
            return

        # ---- 提示词 (组合判断) ----
        hint, hint_color = self._get_hint(rc_text, fs_text, ch, s)
        if not self._goal_z_safe:
            hint = f"{hint} {self._goal_safety_msg}"
            hint_color = "#f38ba8"
        self._hint_label.setText(hint)
        self._hint_label.setStyleSheet(f"color: {hint_color}; font-size: 14px; font-weight: bold;")

        self._apply_health_hint()

    def _apply_health_hint(self):
        """扫描节点和话题状态, 有异常则覆盖提示为健康警告"""
        issues = []
        for i in range(self._node_table.rowCount()):
            name = self._node_table.item(i, 0).text()
            status = self._node_table.item(i, 1).text()
            if status == "异常":
                issues.append(f"{name}节点")
        for i in range(self._topic_table.rowCount()):
            tname = self._topic_table.item(i, 0).text()
            status = self._topic_table.item(i, 3).text()
            if status in ("未连接",) or status.startswith("异常"):
                issues.append(f"{tname}话题")
        if issues:
            msg = f"请勿起飞！{'、'.join(issues)}异常，请检查后重启程序！"
            self._hint_label.setText(msg)
            self._hint_label.setStyleSheet("color: #f38ba8; font-size: 14px; font-weight: bold;")
            return True
        return False

    def _get_hint(self, rc_text, fs_text, ch, px4_state):
        hint_text, hint_color = get_hint(rc_text, fs_text, ch, px4_state)
        # 如果返回的是最终状态 (提示11), 锁定提示
        if '状态异常' in hint_text:
            self._hint_locked = True
            self._locked_hint_text = hint_text
        return (hint_text, hint_color)

    @staticmethod
    def _fmt(v):
        return f"{v: 5.2f}"

    def _on_odom(self, msg):
        try:
            p = msg.pose.pose.position
            self._current_pos = (p.x, p.y, p.z)
            now = time.time()
            self._last_odom_time = now
            dt = now - self._last_z_time
            if dt > 0.01:
                self._vz = (p.z - self._last_z) / dt
                self._last_z = p.z
                self._last_z_time = now
            self._pos_label.setText(
                f"x: {self._fmt(p.x)} y: {self._fmt(p.y)} z: {self._fmt(p.z)}")
            self._update_distance()
            self._update_rc_fs_hint()
        except Exception as e:
            print(f"[MONITOR] _on_odom error: {e}", file=sys.stderr)

    def _on_goal(self, msg):
        try:
            p = msg.pose.position
            self._goal_pos = (p.x, p.y, p.z)
            gz = p.z
            if gz > VIRTUAL_CEIL:
                self._goal_z_safe = False
                self._goal_safety_msg = f"点位高度超过安全天花板 ({gz:.1f}m > {VIRTUAL_CEIL}m)！"
                self._goal_label.setText(
                    f"x: {self._fmt(p.x)} y: {self._fmt(p.y)} z: {self._fmt(p.z)}")
                self._update_rc_fs_hint()
            elif gz < VIRTUAL_GROUND:
                self._goal_z_safe = False
                self._goal_safety_msg = f"点位低于安全地板 ({gz:.1f}m < {VIRTUAL_GROUND}m)！"
                self._goal_label.setText(
                    f"x: {self._fmt(p.x)} y: {self._fmt(p.y)} z: {self._fmt(p.z)}")
                self._update_rc_fs_hint()
            else:
                self._goal_z_safe = True
                self._goal_safety_msg = ""
                self._goal_label.setText(
                    f"x: {self._fmt(p.x)} y: {self._fmt(p.y)} z: {self._fmt(p.z)}")
                self._update_rc_fs_hint()
            self._update_distance()
        except Exception as e:
            print(f"[MONITOR] _on_goal error: {e}", file=sys.stderr)

    def _update_distance(self):
        if self._goal_pos is None:
            return
        dx = self._current_pos[0] - self._goal_pos[0]
        dy = self._current_pos[1] - self._goal_pos[1]
        dz = self._current_pos[2] - self._goal_pos[2]
        self._dist_label.setText(f"{math.sqrt(dx*dx + dy*dy + dz*dz):.2f} m")

    def _on_topic_hz(self, topic, hz):
        try:
            self._topic_hz[topic] = hz
            for i, (_, tname, expected) in enumerate(self._monitor_topics):
                if tname == topic:
                    item = self._topic_table.item(i, 2)
                    item.setText(f"{hz:.1f} Hz")
                    item.setTextAlignment(Qt.AlignCenter)
                    st = self._topic_table.item(i, 3)
                    if hz < 0.1:
                        st.setText("未连接")
                        st.setForeground(QColor("#f38ba8"))
                    elif abs(hz - expected) / expected > 0.3:
                        st.setText(f"异常({expected:.0f}Hz)")
                        st.setForeground(QColor("#f9e2af"))
                    else:
                        st.setText("正常")
                        st.setForeground(QColor("#a6e3a1"))
                    st.setTextAlignment(Qt.AlignCenter)
                    break
        except Exception as e:
            print(f"[MONITOR] _on_topic_hz error: {e}", file=sys.stderr)

    def _check_topic_timeout(self):
        """定期检查话题是否超时未收到消息, 强制更新为未连接"""
        now = time.time()
        for i, (_, tname, expected) in enumerate(self._monitor_topics):
            last = self._ros_worker._topic_last_msg_time.get(tname, 0)
            if now - last > 3.0:
                item = self._topic_table.item(i, 2)
                item.setText("0.0 Hz")
                item.setTextAlignment(Qt.AlignCenter)
                st = self._topic_table.item(i, 3)
                st.setText("未连接")
                st.setForeground(QColor("#f38ba8"))
                st.setTextAlignment(Qt.AlignCenter)

        # RC 遥控器超时检测
        rc_last = self._ros_worker._topic_last_msg_time.get('/mavros/rc/in', 0)
        self._rc_timeout = (rc_last > 0 and now - rc_last > 2.0)

        # 里程计超时: 清空位置和距离显示
        if self._last_odom_time > 0 and now - self._last_odom_time > 3.0:
            self._pos_label.setText("x:  --   y:  --   z:  --")
            self._dist_label.setText("--")

        if not self._apply_health_hint():
            self._update_rc_fs_hint()

    def _on_nodes(self, status):
        try:
            for i, name in enumerate(NodeMonitor.KEY_NODES):
                item = self._node_table.item(i, 1)
                if status.get(name, False):
                    item.setText("运行中")
                    item.setForeground(QColor("#a6e3a1"))
                else:
                    item.setText("异常")
                    item.setForeground(QColor("#f38ba8"))
            if not self._apply_health_hint():
                self._update_rc_fs_hint()
        except Exception as e:
            print(f"[MONITOR] _on_nodes error: {e}", file=sys.stderr)

    def _log_event(self, text):
        """系统内部日志 → 仅终端输出, 不显示在面板上"""
        print(f"[MONITOR] {text}", file=sys.stderr)

    def _planner_log(self, text, level=2):
        """diff_planner 规划器日志 → 显示在面板底部"""
        t = time.strftime("%H:%M:%S")
        if level >= 8:  # ERROR
            color = "#f38ba8"
        elif level >= 4:  # WARN
            color = "#f9e2af"
        else:
            color = "#a6adc8"
        html = f'<span style="color:{color};">[{t}] {text}</span><br>'
        self._log_area.moveCursor(QTextCursor.End)
        self._log_area.insertHtml(html)
        # 自动滚动到底部
        self._log_area.moveCursor(QTextCursor.End)

    # ------------------------------------------------------------------
    def closeEvent(self, event):
        if self._ros_worker.isRunning():
            rospy.signal_shutdown("window closed")
            self._ros_worker.quit()
            self._ros_worker.wait(3000)
        event.accept()


# ============================================================================
# 入口
# ============================================================================
def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor("#1e1e2e"))
    palette.setColor(QPalette.WindowText, QColor("#cdd6f4"))
    palette.setColor(QPalette.Base, QColor("#181825"))
    palette.setColor(QPalette.AlternateBase, QColor("#1e1e2e"))
    palette.setColor(QPalette.Text, QColor("#cdd6f4"))
    palette.setColor(QPalette.Button, QColor("#313244"))
    palette.setColor(QPalette.ButtonText, QColor("#cdd6f4"))
    app.setPalette(palette)

    window = MonitorWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
