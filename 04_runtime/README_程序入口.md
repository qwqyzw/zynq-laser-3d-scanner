# 采图 电机 扫描程序入口

本目录包含最近实测快照及本次新增的Python优化和PL接入代码。执行状态不能混淆：

- scan_fixed_exposure.py、fixed_exposure_worker.py：支持显式曝光5/20等硬件允许的整数值；通过PerPoseExposureCamera调用，不直接导入worker。旧PS默认20兼容历史流程，PL适配器默认5。读回、指纹、恢复/释放及质量检查不放宽，见[相机曝光配置](相机曝光配置.md)。
- motor_readonly.py、motor_arduino.py、motor_position.py及两个motor_arduino C文件：已在原base环境实测；需要兼容iop_arduino与9600 Emm。
- motor_uartlite.py：当前14联合发布的转台后端，ARM直接访问AXI UARTLite，9600 8N1。已在同一overlay完成查询、两次1RPM有限移动及停止，无需切换base。
- rotation_scan.py、scan_checked_exposure.py：旧PS识别扫描基线已实测。rotation_scan.run_hardware_scan会加载base，不用于PL联合扫描。
- quality_from_stripes_fast.py、host_image_fast.py：电脑等价与性能已验证，板卡收益待测。
- rig_workflow.py：本包明确调用入口，不自行下载overlay，限制三点10度内；run_small_scan先预检当前画面，合格后才准备电机并运动。本轮没有发送额外电机使能命令。
- pl_scan_camera.py：已完成12次现拍PL提取与0°→5°→0°真实联动，结果见[联动报告](../15_integrated_test_20261020/修复与联动验证结果.md)。PS对照只核验，不替换PL输出；质量审查继续在PS。
- geometric_calibration.py、geometry_core.py、rotation_axis.py、turntable_coordinates.py：几何计算库，不因导入自动进行标定。

rig_config.json把最近像素实验ROI与历史扫描ROI分开。05_geometry给出对应模型及失败记录。相机、台面和光路改变后不能只照抄历史ROI和相位，也不应无目的重做全部标定。

capture_only默认曝光5，只采图；run_small_scan默认曝光5并自动进行质量预检。PL扫描须传入同一 `load_integrated_runtime()` 返回的overlay与driver，显式选择 `backend='PL'`、客户端实际日期及当前ROI `(175,340,400,400)`，使用新的结果目录。不要在扫描期间重新下载配置或另建DMA驱动。保留runtime所有者，异常后需确认驱动恢复成功；DMA恢复未确认时不可退出持有缓冲的进程。

当前验证仅覆盖三角度像素采集与控制流程。08样例和09离线工具可在电脑验证资料配套，不要求接硬件；几何及毫米精度按独立验收记录判断。
