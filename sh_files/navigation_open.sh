#!/bin/zsh
# =============================================================================
# Diff-Navigation 统一启动脚本 —— 开源版 diff_planner
# =============================================================================
setopt shwordsplit    # zsh 默认不拆分变量空格, 此行让 $VAR 像 bash 一样按空格拆成多个参数
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
OPEN_DIR="$PROJECT_DIR/open"
SETUP_FILE="$OPEN_DIR/devel/setup.zsh"
SCRIPTS_DIR="$OPEN_DIR/scripts"


source /opt/ros/noetic/setup.zsh
source "$SETUP_FILE"

# --- 飞行日志 ---
LOGGER_PID=""
LOGGER_ENABLED=$(python3 "$SCRIPTS_DIR/generate_configs.py" logger)

# Ctrl+C 退出时自动清理所有后台进程 (包括日志服务和监视器)
_cleaned=false
cleanup() {
    if $_cleaned; then return; fi
    _cleaned=true
    echo ""
    echo "=== 正在清理所有后台进程 ==="

    # 先通知日志服务停止 (写入结束标记、关闭文件)
    if [ -n "$LOGGER_PID" ] && kill -0 "$LOGGER_PID" 2>/dev/null; then
        kill -TERM "$LOGGER_PID" 2>/dev/null
        sleep 1
    fi

    kill 0 2>/dev/null
    wait 2>/dev/null
    echo "=== 清理完成 ==="
}
trap cleanup EXIT INT TERM HUP

LOC_MODE=$(python3 "$SCRIPTS_DIR/generate_configs.py" loc_mode)
PLANNER_ARGS=$(python3 "$SCRIPTS_DIR/generate_configs.py" planner)
MULTIPOINT_ARGS=$(python3 "$SCRIPTS_DIR/generate_configs.py" multipoint)
CTRL_PARAM_FILE=$(python3 "$SCRIPTS_DIR/generate_configs.py" px4ctrl)

echo "=== Diff-Navigation 开源版 [$LOC_MODE] ==="
echo "Planner args:    $PLANNER_ARGS"
echo "Multipoint args: $MULTIPOINT_ARGS"
echo ""

# ---- 公共: 飞控通信 (先启 ROS master) ----
echo 'nv' | sudo -S chmod 777 /dev/tty* & sleep 1
roslaunch mavros px4.launch & sleep 2
rosrun mavros mavcmd long 511 31 5000 0 0 0 0 0 & sleep 1   # ATTITUDE_QUATERNION
rosrun mavros mavcmd long 511 105 5000 0 0 0 0 0 & sleep 1  # HIGHRES_IMU
rosrun mavros mavcmd long 511 83 5000 0 0 0 0 0 & sleep 1   # ATTITUDE_TARGET
rosrun mavros mavcmd long 511 147 5000 0 0 0 0 0 & sleep 1  # BATTERY_STATUS
rosrun mavros mavcmd long 511 106 5000 0 0 0 0 0 & sleep 1

# ---- 飞行日志 (source 之后启动, 确保自定义消息类型 quadrotor_msgs 可导入) ----
if [ "$LOGGER_ENABLED" = "true" ]; then
    echo "=== Starting flight logger ==="
    python3 "$SCRIPTS_DIR/flight_logger.py" &
    LOGGER_PID=$!
    # 轮询等待 logger 初始化完成 (ROS 发现话题最多10秒, 等待最多20秒)
    LOG_STATE_FILE="$OPEN_DIR/logs/.flight_logger_state.json"
    for i in {1..40}; do
        if [ -f "$LOG_STATE_FILE" ]; then
            LOG_SESSION_PATH=$(python3 -c "import json; print(json.load(open('$LOG_STATE_FILE')).get('session_path',''))" 2>/dev/null || echo "")
            if [ -n "$LOG_SESSION_PATH" ]; then
                echo "Log session: $LOG_SESSION_PATH"
                # 将本脚本的后续 stdout/stderr 同步写入日志
                exec > >(tee -a "$LOG_SESSION_PATH/navigation_stdout.log") 2>&1
                break
            fi
        fi
        sleep 0.5
    done
    if [ ! -f "$LOG_STATE_FILE" ]; then
        echo "WARNING: flight_logger 初始化超时, 跳过 stdout 记录"
    fi
    echo ""
fi


# ---- 定位 & 规划 ----
if [ "$LOC_MODE" = "vio" ]; then
    # ---------- VIO ----------
    export DRONE_ID=0
    roslaunch realsense2_camera rs_camera.launch & sleep 10
    roslaunch vins vins_d435.launch & sleep 5
    roslaunch diff_planner run_exp_single_vio.launch $PLANNER_ARGS & sleep 3
    roslaunch px4ctrl run_ctrl_vio.launch ctrl_param_file:="$CTRL_PARAM_FILE" & sleep 3
    roslaunch multipoint multipointplan_exp_vio.launch $MULTIPOINT_ARGS & sleep 2
else
    # ---------- LIO (默认) ----------
    LIDAR_LAUNCH=$(python3 "$SCRIPTS_DIR/generate_configs.py" lidar)
    roslaunch faster_lio "$LIDAR_LAUNCH.launch" & sleep 8
    roslaunch ekf ekf_lidar.launch & sleep 3
    roslaunch diff_planner run_exp_single_lio.launch $PLANNER_ARGS & sleep 3
    roslaunch px4ctrl run_ctrl_lio.launch ctrl_param_file:="$CTRL_PARAM_FILE" & sleep 3
    roslaunch multipoint multipointplan_exp_lio.launch $MULTIPOINT_ARGS & sleep 2
fi

# ---- 公共: 可视化 ----
rospack list 2>/dev/null | grep -q '^usb_cam ' && roslaunch usb_cam usb_cam.launch & sleep 3 || echo "usb_cam 未安装, 跳过"
roslaunch diff_planner exp_rviz.launch & sleep 1

# ---- 监控面板 ----
MONITOR_ENABLED=$(python3 "$SCRIPTS_DIR/generate_configs.py" monitor)
if [ "$MONITOR_ENABLED" = "true" ]; then
    echo "=== Starting monitor dashboard ==="
    python3 "$SCRIPTS_DIR/monitor_dashboard.py" &
    MONITOR_PID=$!
fi

wait
