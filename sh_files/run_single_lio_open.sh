#!/bin/zsh
# =============================================================================
# LIO 单机导航启动脚本 —— 开源版 diff_planner
# =============================================================================

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
WS_DIR="$PROJECT_DIR/open"
SETUP_FILE="$WS_DIR/devel/setup.zsh"


# --- 1. 环境加载 -----------------------------------------------------------
source /opt/ros/noetic/setup.zsh
source "$SETUP_FILE"

echo "[信息] 开源版 diff_planner 启动，工作空间: $WS_DIR"


# --- 2. 必需 ROS 包检查（缺失即退出） -------------------------------------
need_pkg() {
    if ! rospack find "$1" >/dev/null 2>&1; then
        echo "[错误] 找不到 ROS 包 '$1'，请检查工作空间环境配置。"
        exit 1
    fi
}
for pkg in mavros faster_lio ekf diff_planner px4ctrl multipoint; do
    need_pkg "$pkg"
done

# --- 3. 启动链路（与原 run_single_lio.sh 一致） -----------------------------
echo 'nv' | sudo -S chmod 777 /dev/tty* & sleep 1;
roslaunch mavros px4.launch & sleep 2;
rosrun mavros mavcmd long 511 31 5000 0 0 0 0 0 & sleep 1;   # ATTITUDE_QUATERNION
rosrun mavros mavcmd long 511 105 5000 0 0 0 0 0 & sleep 1;  # HIGHRES_IMU
rosrun mavros mavcmd long 511 83 5000 0 0 0 0 0 & sleep 1;   # ATTITUDE_TARGET
rosrun mavros mavcmd long 511 147 5000 0 0 0 0 0 & sleep 1;  # BATTERY_STATUS
rosrun mavros mavcmd long 511 106 5000 0 0 0 0 0 & sleep 1;
roslaunch faster_lio mapping_mid360.launch & sleep 8;
roslaunch ekf ekf_lidar.launch & sleep 3;
roslaunch diff_planner run_exp_single_lio.launch & sleep 3;
roslaunch px4ctrl run_ctrl_lio.launch & sleep 3;
roslaunch multipoint multipointplan_exp_lio.launch & sleep 2;
roslaunch diff_planner exp_rviz.launch & sleep 1;
wait;
