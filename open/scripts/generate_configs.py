#!/usr/bin/env python3
"""
从 navigation_config.yaml 生成各模块所需的临时配置文件

用法:
  generate_configs.py px4ctrl     # 生成 px4ctrl 临时 yaml, 打印路径
  generate_configs.py planner     # 打印规划器 roslaunch args
  generate_configs.py multipoint  # 打印多点巡航 roslaunch args
"""

import sys
import os
import yaml
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
CONFIG_PATH = os.path.join(PROJECT_ROOT, 'config', 'navigation_config.yaml')
SRC_DIR = os.path.join(PROJECT_ROOT, 'src')

# Track generated temp files for cleanup
_temp_files = []


def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return yaml.safe_load(f)


def generate_px4ctrl():
    """Generate temporary px4ctrl param yaml, override user-facing params."""
    config = load_config()
    fc = config.get('flight_control', {})

    # Load base px4ctrl config
    base_path = os.path.join(SRC_DIR, 'px4ctrl', 'config', 'ctrl_param_fpv.yaml')
    with open(base_path, 'r') as f:
        px4_params = yaml.safe_load(f)

    # Override user-facing params
    if 'takeoff_height' in fc:
        px4_params.setdefault('auto_takeoff_land', {})['takeoff_height'] = fc['takeoff_height']
    if 'takeoff_land_speed' in fc:
        px4_params.setdefault('auto_takeoff_land', {})['takeoff_land_speed'] = fc['takeoff_land_speed']
    if 'mass' in fc:
        px4_params['mass'] = fc['mass']
    if 'gra' in fc:
        px4_params['gra'] = fc['gra']
    if 'max_manual_vel' in fc:
        px4_params['max_manual_vel'] = fc['max_manual_vel']
    if 'max_angle' in fc:
        px4_params['max_angle'] = fc['max_angle']
    if 'low_voltage' in fc:
        px4_params['low_voltage'] = fc['low_voltage']

    # Override gains if provided
    gain = fc.get('gain', {})
    if gain:
        px4_params.setdefault('gain', {}).update(gain)

    # Write to temp file
    tf = tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False, prefix='px4ctrl_')
    yaml.dump(px4_params, tf, default_flow_style=False)
    tf.close()
    _temp_files.append(tf.name)
    print(tf.name)


def generate_planner_args():
    """Output roslaunch args for diff_planner."""
    config = load_config()
    pl = config.get('planner', {})

    args = []
    args.append(f'max_vel:={pl.get("max_vel", 1.5)}')
    args.append(f'max_acc:={pl.get("max_acc", 3.0)}')
    args.append(f'max_jer:={pl.get("max_jer", 20.0)}')
    args.append(f'yaw_mode:={pl.get("yaw_mode", 0)}')
    args.append(f'yaw_dot_max:={pl.get("yaw_dot_max", 3.0)}')
    args.append(f'yaw_dot_dot_max:={pl.get("yaw_dot_dot_max", 3.0)}')
    args.append(f'planning_horizon:={pl.get("planning_horizon", 7.5)}')
    args.append(f'map_resolution:={pl.get("map_resolution", 0.2)}')
    args.append(f'inflation_size:={pl.get("inflation_size", 0.4)}')
    args.append(f'local_map_size_x:={pl.get("local_map_size_x", 5.5)}')
    args.append(f'local_map_size_y:={pl.get("local_map_size_y", 5.5)}')
    args.append(f'local_map_size_z:={pl.get("local_map_size_z", 2.0)}')

    enable_vw = pl.get('enable_virtual_wall', True)
    args.append(f'enable_virtual_wall:={"true" if enable_vw else "false"}')
    args.append(f'virtual_ceil:={pl.get("virtual_ceil", 2.0)}')
    args.append(f'virtual_ground:={pl.get("virtual_ground", 0.1)}')
    args.append(f'init_x:={pl.get("init_x", 0.0)}')
    args.append(f'init_y:={pl.get("init_y", 0.0)}')
    args.append(f'init_z:={pl.get("init_z", 0.0)}')

    cam = pl.get('camera', {})
    if cam:
        args.append(f'cx:={cam.get("cx", 319.3)}')
        args.append(f'cy:={cam.get("cy", 239.1)}')
        args.append(f'fx:={cam.get("fx", 386.3)}')
        args.append(f'fy:={cam.get("fy", 386.3)}')

    print(' '.join(args))


def generate_multipoint_args():
    """Output roslaunch args for multipoint + 生成航点yaml."""
    config = load_config()
    mp = config.get('multipoint', {})

    # 生成临时 points.yaml (包含 navigation_config 中的航点)
    points_yaml = ["points:"]
    for pt in mp.get('points', []):
        if len(pt) >= 5:
            points_yaml.append(f"  - [{pt[0]}, {pt[1]}, {pt[2]}, {pt[3]}, {pt[4]}]")
        elif len(pt) >= 4:
            points_yaml.append(f"  - [{pt[0]}, {pt[1]}, {pt[2]}, {pt[3]}]")
        else:
            points_yaml.append(f"  - [{pt[0]}, {pt[1]}, {pt[2]}]")
    points_yaml.append("back_points:")
    for pt in mp.get('back_points', []):
        points_yaml.append(f"  - [{pt[0]}, {pt[1]}, {pt[2]}]")

    tf = tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False, prefix='points_')
    tf.write('\n'.join(points_yaml))
    tf.close()
    _temp_files.append(tf.name)

    args = []
    args.append(f'yaml_path:={tf.name}')
    args.append(f'next_distance:={mp.get("next_distance", 0.1)}')
    auto_plan = '1' if mp.get('auto_planning', False) else '0'
    args.append(f'auto_planning:={auto_plan}')
    auto_land = '1' if mp.get('auto_landing', False) else '0'
    args.append(f'auto_landing:={auto_land}')
    enable_ya = 'true' if mp.get('enable_yaw_align', False) else 'false'
    args.append(f'enable_yaw_align:={enable_ya}')
    args.append(f'yaw_align_thresh:={mp.get("yaw_align_thresh", 1.8)}')

    print(' '.join(args))


def generate_lidar_args():
    """Output the LiDAR launch file name based on model."""
    config = load_config()
    lidar = config.get('lidar', {})
    model = lidar.get('model', 'mid360')

    mapping = {
        'mid360': 'mapping_mid360',
        'avia': 'mapping_avia',
        'horizon': 'mapping_horizon',
        'velodyne': 'mapping_velodyne',
        'hesai': 'mapping_hesai',
        'ouster64': 'mapping_ouster64',
    }
    print(mapping.get(model, 'mapping_mid360'))


def generate_localization_mode():
    """Output 'lio' or 'vio'."""
    config = load_config()
    print(config.get('localization_mode', 'lio'))


def generate_monitor_enabled():
    """Output 'true' or 'false' for monitor.enabled config."""
    config = load_config()
    monitor = config.get('monitor', {})
    print('true' if monitor.get('enabled', True) else 'false')


def generate_logger_enabled():
    """Output 'true' or 'false' for logging.enabled config."""
    config = load_config()
    logging = config.get('logging', {})
    print('true' if logging.get('enabled', True) else 'false')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: generate_configs.py <px4ctrl|planner|multipoint|lidar|monitor|logger|loc_mode>", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == 'px4ctrl':
        generate_px4ctrl()
    elif cmd == 'planner':
        generate_planner_args()
    elif cmd == 'multipoint':
        generate_multipoint_args()
    elif cmd == 'lidar':
        generate_lidar_args()
    elif cmd == 'monitor':
        generate_monitor_enabled()
    elif cmd == 'logger':
        generate_logger_enabled()
    elif cmd == 'loc_mode':
        generate_localization_mode()
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)
