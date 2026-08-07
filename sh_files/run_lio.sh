#!/bin/zsh
# =============================================================================
# LIO 建图启动（faster_lio + ekf + rviz），不涉及规划器
# =============================================================================
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
OPEN_DIR="$PROJECT_DIR/open"
SETUP_FILE="$OPEN_DIR/devel/setup.zsh"


source /opt/ros/noetic/setup.zsh
source "$SETUP_FILE"


echo 'nv' | sudo -S chmod 777 /dev/tty* & sleep 1;
roslaunch mavros px4.launch & sleep 6;
rosrun mavros mavcmd long 511 105 5000 0 0 0 0 0 & sleep 1;
rosrun mavros mavcmd long 511 31 5000 0 0 0 0 0 & sleep 1;
roslaunch faster_lio mapping_mid360.launch & sleep 5;
roslaunch ekf ekf_lidar.launch & sleep 5;
roslaunch diff_planner exp_rviz.launch & sleep 1;
wait;
