#!/bin/zsh
# =============================================================================
# 单机 LIO + 8fly 轨迹执行
# =============================================================================
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
OPEN_DIR="$PROJECT_DIR/open"
SETUP_FILE="$OPEN_DIR/devel/setup.zsh"


source /opt/ros/noetic/setup.zsh
source "$SETUP_FILE"


echo 'nv' | sudo -S chmod 777 /dev/tty* & sleep 1;
roslaunch mavros px4.launch & sleep 2;
rosrun mavros mavcmd long 511 31 5000 0 0 0 0 0 & sleep 1;   # ATTITUDE_QUATERNION
rosrun mavros mavcmd long 511 105 5000 0 0 0 0 0 & sleep 1;  # HIGHRES_IMU
rosrun mavros mavcmd long 511 83 5000 0 0 0 0 0 & sleep 1;   # ATTITUDE_TARGET
rosrun mavros mavcmd long 511 147 5000 0 0 0 0 0 & sleep 1;  # BATTERY_STATUS
rosrun mavros mavcmd long 511 106 5000 0 0 0 0 0 & sleep 1;
roslaunch faster_lio mapping_mid360.launch & sleep 10;
roslaunch ekf ekf_lidar.launch & sleep 5;
# roslaunch diff_planner run_exp_single_lio.launch & sleep 3;
roslaunch px4ctrl run_ctrl_lio.launch & sleep 3;
# roslaunch multipoint multipointplan_exp_lio.launch & sleep 2;
# roslaunch diff_planner exp_rviz.launch & sleep 1;
cd ~/8fly;
source devel/setup.zsh;
roslaunch trajectory fly_test.launch & sleep 3;

wait;
