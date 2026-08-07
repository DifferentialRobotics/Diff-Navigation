#!/bin/zsh
# =============================================================================
# 一键起飞（/px4ctrl/takeoff_land 命令 1）
# =============================================================================
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
OPEN_DIR="$PROJECT_DIR/open"
SETUP_FILE="$OPEN_DIR/devel/setup.zsh"

if [ ! -f "$SETUP_FILE" ]; then
    echo "[错误] 未找到开源工作空间环境文件: $SETUP_FILE"
    echo "       请先编译 open 工作空间。"
    exit 1
fi
source /opt/ros/noetic/setup.zsh
source "$SETUP_FILE"

rostopic pub -1  /px4ctrl/takeoff_land quadrotor_msgs/TakeoffLand "takeoff_land_cmd: 1"
