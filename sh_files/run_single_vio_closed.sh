#!/bin/zsh
# =============================================================================
# VIO 单机导航启动脚本 —— 闭源版 diff_planner（算法部）
# =============================================================================
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
OPEN_DIR="$PROJECT_DIR/open"
CLOSED_DIR="$PROJECT_DIR/closed"
OPEN_SETUP="$OPEN_DIR/devel/setup.zsh"
CLOSED_SETUP="$CLOSED_DIR/install/setup.zsh"


source /opt/ros/noetic/setup.zsh
source "$OPEN_SETUP"
source "$CLOSED_SETUP"
# 闭源包来源校验（必须解析到 closed/install）
for pkg in diff_planner multipoint; do
    pkg_path="$(rospack find "$pkg" 2>/dev/null)"
    case "$pkg_path" in
        "$CLOSED_DIR/install/"*) : ;;
        *)
            echo "[错误] 包 '$pkg' 解析到 '$pkg_path'，而非闭源 install 空间！"
            exit 1
            ;;
    esac
done
# VIO 版 launch 使用 $(env DRONE_ID)，未设置时 roslaunch 会报错退出
export DRONE_ID="${DRONE_ID:-0}"

echo 'nv' | sudo -S chmod 777 /dev/tty* & sleep 1;
roslaunch mavros px4.launch & sleep 2;
rosrun mavros mavcmd long 511 31 5000 0 0 0 0 0 & sleep 1;   # ATTITUDE_QUATERNION
rosrun mavros mavcmd long 511 105 5000 0 0 0 0 0 & sleep 1;  # HIGHRES_IMU
rosrun mavros mavcmd long 511 83 5000 0 0 0 0 0 & sleep 1;   # ATTITUDE_TARGET
rosrun mavros mavcmd long 511 147 5000 0 0 0 0 0 & sleep 1;  # BATTERY_STATUS
rosrun mavros mavcmd long 511 106 5000 0 0 0 0 0 & sleep 1;
roslaunch realsense2_camera rs_camera.launch & sleep 10;
roslaunch vins vins_d435.launch & sleep 5;
roslaunch diff_planner run_exp_single_vio.launch & sleep 3;
roslaunch px4ctrl run_ctrl_vio.launch & sleep 3;
roslaunch multipoint multipointplan_exp_vio.launch & sleep 2;
roslaunch diff_planner exp_rviz.launch & sleep 1;
wait;
