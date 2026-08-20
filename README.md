# Diff-Navigation v1.2.1

## 概述
**Diff-Navigation** 是为**微分智飞**公司旗下教育无人机子品牌**非凸空间**适配的导航避障算法。其基于开源算法 **[EGO-Planner-v2](https://github.com/ZJU-FAST-Lab/EGO-Planner-v2)** ，并由原班人马深度参与算法优化。在继承 **EGO-Planner** 优秀框架的基础上，针对教育无人机平台的特殊需求进行了全面适配和增强，旨在提供更稳定、更可靠的科研体验。

## 实机运行步骤

### 1.拉取代码
  ```
  git clone --recursive -b v1.2.1 https://gitee.com/DifferentialRobotics/Diff-Navigation.git
  ```
 
### 2.编译开源代码
  ```
  cd ~/Diff-Navigation/open
  catkin_make
  ```

### 3.配置雷达外参

1. 在`~/Diff-Navigation/open/src/faster-lio/faster-lio/config/mid360.yaml`文件中配置好雷达外参`extrinsic_T`和`extrinsic_R`，并启动雷达定位脚本测试定位是否正常：
    ```
    cd ~/Diff-Navigation
    ./sh_files/run_lio.sh
    ```
2. 启动脚本后拿起无人机在空中晃动，并沿飞行场地行走一圈后放回原地，查看定位是否稳定。
    
### 4. 雷达定位自主避障飞行（开源版）


1. 在启动程序之前，请务必核对关键参数。相关配置项位于`~/Diff-Navigation/open/config/navigation_config.yaml`文件中。将`localization_mode`参数修改为 `lio`，以将定位模式切换为激光雷达定位。
   
2. 打开遥控器，确认拨杆位置是否正确。

3. 进入 Diff-Navigation 目录并启动开源版脚本
    ```
    cd ~/Diff-Navigation
    ./sh_files/navigation_open.sh
    ```
    > 注意：若没有执行规划任务需求只是测试程序，请保持无人机处于急停状态，以免误触遥控器导致无人机自动起飞！！！

4. 等待约 50 秒，程序完全启动后将自动打开 monitor 用户交互界面，并同时启动 RViz 可视化窗口。


5. 起飞：
    ```
    cd ~/Diff-Navigation
    ./sh_files/takeoff.sh
    ```

6. 执行规划任务：
    ```
    cd ~/Diff-Navigation
    ./sh_files/pub_trigger.sh
    ```

7. 返程规划：
    ```
    cd ~/Diff-Navigation
    ./sh_files/back.sh
    ```

8. 降落：
    ```
    cd ~/Diff-Navigation
    ./sh_files/land.sh
    ```
    > 注意：待无人机落地后及时锁桨并在任务终端输入 ctrl+c 结束任务。
    > 起飞/执行规划任务/返程规划/降落也可使用遥控器控制，详见配套产品手册。




   
### 5. 雷达定位自主避障飞行（闭源版）


1. 在配置文件 `~/Diff-Navigation/closed/install/share/multipoint/launch/multipointplan_exp_lio.launch`中查看设置的飞行模式

    >+  `fligt_type` 为 1 表示选择 `test1` 模式进行多点规划，即依次经过各途经点

    >+  `fligt_type` 为 2 表示选择 `test2` 模式进行多点规划，可以控制到达各途径点后的停留时间

    >+  其余模式的定义可在 `~/Diff-Navigation/closed/install/share/multipoint/config/points.yaml`中找到
    
    >+  当设置 `auto_planning` 为 0 时，起飞悬停后，需要手动触发才会开始规划路径
    
    >+  当设置 `auto_landing` 为 0 时，到达最后一个目标点后，需要手动触发才会开始降落

2. 在`~/Diff-Navigation/closed/install/share/multipoint/config/points.yaml`中设置 `test1` 对应的途径点坐标，以及返航点坐标。

3. 在`~/Diff-Navigation/closed/install/share/diff_planner/launch/exp/run_exp_single_lio.launch`中设置`max_vel`最大飞行速度。

4. 打开遥控器，确认拨杆位置是否正确。

5. 进入 Diff-Navigation 目录并启动闭源版雷达定位导航脚本
    ```
    cd ~/Diff-Navigation
    ./sh_files/run_single_lio_closed.sh
    ```
    > 注意：若没有执行规划任务需求只是测试程序，请保持无人机处于急停状态，以免误触遥控器导致无人机自动起飞！！！



6. 等待约 50 秒，程序完全启动后将自动启动 RViz 可视化窗口。


7. 起飞：
    ```
    cd ~/Diff-Navigation
    ./sh_files/takeoff.sh
    ```

8. 执行规划任务：
    ```
    cd ~/Diff-Navigation
    ./sh_files/pub_trigger.sh
    ```

9. 返程规划：
    ```
    cd ~/Diff-Navigation
    ./sh_files/back.sh
    ```

10. 降落：
    ```
    cd ~/Diff-Navigation
    ./sh_files/land.sh
    ```
    > 注意：待无人机落地后及时锁桨并在任务终端输入 ctrl+c 结束任务。
    > 起飞/执行规划任务/返程规划/降落也可使用遥控器控制，详见配套产品手册。





## 致谢与声明
本项目在开发过程中参考并使用了以下开源项目：
- **[EGO-Planner-v2](https://github.com/ZJU-FAST-Lab/EGO-Planner-v2)**，特此感谢浙江大学 **FAST-Lab** 团队的开源贡献。
- **[Faster-LIO](https://github.com/gaoxiang12/faster-lio)** ，特此感谢项目作者团队的开源贡献。
- **[livox_ros_driver2](https://github.com/Livox-SDK/livox_ros_driver2)** 驱动，特此感谢项目作者团队的开源贡献。
- **[VINS-Fusion-gpu](https://github.com/pjrambo/VINS-Fusion-gpu)**，特此感谢项目作者团队的开源贡献。
- **[realsense-ros](https://github.com/realsenseai/realsense-ros)** 驱动，特此感谢项目作者团队的开源贡献。

相关代码均严格遵循原项目的开源许可协议使用，用户在使用本项目时，请务必遵守相应的许可证条款。

# Q&A
请随时提交问题或讨论，我们会在看到问题后尽快回复。
