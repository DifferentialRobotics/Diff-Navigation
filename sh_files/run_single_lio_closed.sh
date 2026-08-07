#!/bin/zsh
# =============================================================================
# LIO 单机导航启动脚本 —— 闭源版 diff_planner（算法部）
# =============================================================================

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
OPEN_WS_DIR="$PROJECT_DIR/open"
CLOSED_WS_DIR="$PROJECT_DIR/closed"
OPEN_SETUP="$OPEN_WS_DIR/devel/setup.zsh"
CLOSED_SETUP="$CLOSED_WS_DIR/install/setup.zsh"
# 闭源规划器主程序（install 空间可执行文件，不依赖源码）
CLOSED_NODE="$CLOSED_WS_DIR/install/lib/diff_planner/diff_planner_node"



# --- 1. 环境加载 -----------------------------------------------------------
# 顺序重要：先 open 后 closed，闭源包在 ROS_PACKAGE_PATH 中优先生效；
# 开源版同名包（diff_planner / multipoint）会被闭源版遮蔽，属预期行为。
source /opt/ros/noetic/setup.zsh
source "$OPEN_SETUP"
source "$CLOSED_SETUP"

# 闭源版部分 launch（如 VIO 版）通过 $(env DRONE_ID) 读取编号，未设置会导致
# roslaunch 报错退出；这里统一预设为 0（LIO 版固定为 0，不受影响）。
export DRONE_ID="${DRONE_ID:-0}"

echo "[信息] 闭源版 diff_planner 启动，规划器来自: $CLOSED_WS_DIR/install"
echo "[信息] 公共包（faster_lio/ekf/px4ctrl）来自: $OPEN_WS_DIR/devel"


# --- 2. 必需 ROS 包检查（缺失即退出） -------------------------------------
need_pkg() {
    if ! rospack find "$1" >/dev/null 2>&1; then
        echo "[错误] 找不到 ROS 包 '$1'，请检查工作空间环境配置。"
        exit 1
    fi
}
# 闭源包：必须解析到 closed/install（检查来源是否正确）
for pkg in diff_planner multipoint; do
    need_pkg "$pkg"
    pkg_path="$(rospack find "$pkg")"
    case "$pkg_path" in
        "$CLOSED_WS_DIR/install/"*) : ;;
        *)
            echo "[错误] 包 '$pkg' 解析到 '$pkg_path'，而非闭源 install 空间！"
            echo "       请确认本脚本运行环境中未提前 source 过其它包含该包的工作空间。"
            exit 1
            ;;
    esac
done
# 公共包：来自 open 工作空间或系统
for pkg in mavros faster_lio ekf px4ctrl; do
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
