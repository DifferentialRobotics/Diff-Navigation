#!/usr/bin/env python3
"""
YAML配置解析工具 - 从 navigation_config.yaml 读取参数
用法:
  parse_config.py <key_path>
示例:
  parse_config.py flight_control.takeoff_height    # → 0.8
  parse_config.py planner.max_vel                  # → 1.5
  parse_config.py multipoint.points                # → JSON array
  parse_config.py --bash                           # → 输出 bash 可eval的变量
"""

import sys
import os
import yaml

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'config', 'navigation_config.yaml'
)


def get_value(config, key_path):
    keys = key_path.split('.')
    val = config
    for k in keys:
        if isinstance(val, dict):
            val = val[k]
        elif isinstance(val, list):
            val = val[int(k)]
        else:
            return None
    return val


def format_value(val):
    if isinstance(val, bool):
        return 'true' if val else 'false'
    elif isinstance(val, (int, float)):
        return str(val)
    elif isinstance(val, list):
        return str(val)
    elif isinstance(val, str):
        return val
    return str(val)


def main():
    with open(CONFIG_PATH, 'r') as f:
        config = yaml.safe_load(f)

    if len(sys.argv) < 2:
        print("Usage: parse_config.py <key_path>", file=sys.stderr)
        sys.exit(1)

    if sys.argv[1] == '--bash':
        # Output bash-compatible variable assignments
        fc = config.get('flight_control', {})
        pl = config.get('planner', {})
        mp = config.get('multipoint', {})
        lidar = config.get('lidar', {})
        monitor = config.get('monitor', {})

        print(f'TAKEOFF_HEIGHT={fc.get("takeoff_height", 0.8)}')
        print(f'TAKEOFF_LAND_SPEED={fc.get("takeoff_land_speed", 0.2)}')
        print(f'MAX_VEL={pl.get("max_vel", 1.5)}')
        print(f'MAX_ACC={pl.get("max_acc", 3.0)}')
        print(f'MAP_RESOLUTION={pl.get("map_resolution", 0.2)}')
        print(f'INFLATION_SIZE={pl.get("inflation_size", 0.4)}')

        enable_vw = pl.get('enable_virtual_wall', True)
        print(f'ENABLE_VIRTUAL_WALL={"true" if enable_vw else "false"}')
        print(f'VIRTUAL_CEIL={pl.get("virtual_ceil", 2.0)}')
        print(f'VIRTUAL_GROUND={pl.get("virtual_ground", 0.1)}')
        print(f'LOCAL_MAP_X={pl.get("local_map_size_x", 5.5)}')
        print(f'LOCAL_MAP_Y={pl.get("local_map_size_y", 5.5)}')
        print(f'LOCAL_MAP_Z={pl.get("local_map_size_z", 2.0)}')
        print(f'INIT_X={pl.get("init_x", 0.0)}')
        print(f'INIT_Y={pl.get("init_y", 0.0)}')
        print(f'INIT_Z={pl.get("init_z", 0.0)}')

        print(f'NEXT_DISTANCE={mp.get("next_distance", 0.1)}')
        auto_plan = mp.get('auto_planning', False)
        print(f'AUTO_PLANNING={1 if auto_plan else 0}')
        auto_land = mp.get('auto_landing', False)
        print(f'AUTO_LANDING={1 if auto_land else 0}')
        enable_ya = mp.get('enable_yaw_align', False)
        print(f'ENABLE_YAW_ALIGN={"true" if enable_ya else "false"}')
        print(f'YAW_ALIGN_THRESH={mp.get("yaw_align_thresh", 1.8)}')

        print(f'LIDAR_MODEL={lidar.get("model", "mid360")}')
        print(f'LOC_MODE={config.get("localization_mode", "lio")}')
        mon_enabled = monitor.get('enabled', True)
        print(f'MONITOR_ENABLED={"true" if mon_enabled else "false"}')
    else:
        val = get_value(config, sys.argv[1])
        print(format_value(val))


if __name__ == '__main__':
    main()
