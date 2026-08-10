#!/bin/zsh
# =============================================================================
# 集群启动脚本 —— 开源版 diff_planner
# =============================================================================
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
OPEN_DIR="$PROJECT_DIR/open"
SETUP_FILE="$OPEN_DIR/devel/setup.zsh"


source /opt/ros/noetic/setup.zsh
source "$SETUP_FILE"


export DRONE_ID=0;
export INIT_X=-2.0; export INIT_Y=1.0; export INIT_Z=0.0;

export FLIGHT_TYPE=2;
export POINT_NUM=2;
export POINT0_X=4.0; export POINT0_Y=0.0; export POINT0_Z=1.0;
export POINT1_X=0.0; export POINT1_Y=0.0; export POINT1_Z=0.8;
export POINT2_X=3.5; export POINT2_Y=0.0; export POINT2_Z=1.0;
export POINT3_X=3.5; export POINT3_Y=0.0; export POINT3_Z=1.0;
export POINT4_X=3.5; export POINT4_Y=0.0; export POINT4_Z=1.0;

export DRONE_NUM=3;
export DRONE_IP_0=192.168.8.158;
export DRONE_IP_1=192.168.8.191;
export DRONE_IP_2=192.168.8.195;
export DRONE_IP_3=192.168.8.193;
export DRONE_IP_4=192.168.8.194;
export GS_IP=192.168.8.110;

echo 'nv' | sudo -S chmod 777 /dev/tty* & sleep 1;
roslaunch mavros px4.launch & sleep 4;
rosrun mavros mavcmd long 511 31 5000 0 0 0 0 0 & sleep 1;   # ATTITUDE_QUATERNION
rosrun mavros mavcmd long 511 105 5000 0 0 0 0 0 & sleep 1;  # HIGHRES_IMU
rosrun mavros mavcmd long 511 83 5000 0 0 0 0 0 & sleep 1;   # ATTITUDE_TARGET
rosrun mavros mavcmd long 511 147 5000 0 0 0 0 0 & sleep 1;  # BATTERY_STATUS
rosrun mavros mavcmd long 511 106 5000 0 0 0 0 0 & sleep 1;
roslaunch faster_lio mapping_mid360.launch & sleep 5;
roslaunch ekf ekf_lidar_swarm.launch & sleep 2;
roslaunch swarm_bridge bridge_tcp_drone.launch & sleep 3;
roslaunch diff_planner run_exp_swarm.launch & sleep 3;
roslaunch px4ctrl run_ctrl_lio.launch & sleep 3;
# roslaunch diff_planner exp_rviz.launch & sleep 1;
wait;
