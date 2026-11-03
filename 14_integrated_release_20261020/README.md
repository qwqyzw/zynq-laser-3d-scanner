# PL条纹提取＋转台UART联合配置

客户端发布日期：2026年10月20日。此目录是新增版本；06_fpga_verified继续保存原静态发布。

laser_motor.bit和laser_motor.hwh由同一次Vivado2026.1构建导出后一起改名，必须配套使用。
识别RTL与旧静态版相同；增加AXI UARTLite：0x42C00000，9600波特率、8数据位、无校验、1停止位。
Arduino D1/U12是板端TX，D0/T14是板端RX；两者LVCMOS33。原激光核0x43C00000、DMA0x40400000和50MHz不变。

implementation保留实际布线后时序、资源、DRC、引脚和时钟报告。最终DRC的0 Error/0 Critical Warning不等于整个构建日志没有警告；官方PS7预设的DDR未用lane提示保留。

SHA256.json绑定本次bit/HWH、实现报告和对应源码。板测结果单独记录到15_integrated_test_20261020；文件校验通过本身不等于实机验收通过。

电脑检查：
```
python 04_runtime/pl_runtime.py --integrated
python 09_tools/check_integrated_release.py
```

板上在干净Python进程中，通过04_runtime/pl_runtime.py的load_integrated_runtime()显式加载本配置，保留返回runtime与同一个driver，再传入rig_workflow.run_small_scan()。run_small_scan默认曝光5、三角度0/5/0、1RPM，先验证静态图像质量，再准备转台；质量失败即取消后续角度。ROI必须对应当前取景；本次是(175,340,400,400)。

09_tools/run_integrated_board.py默认仅检查文件；显式--run-board会运行56次静态事务和小范围联动。该首次集成测试入口要求先前停在已验证base恢复配置，会先只读确认停稳，再仅加载一次新配置。后续在同一Python进程中复用runtime即可，不能交替下载base和本配置冒充集成成功。

原始图像、实际PL输出、PS对照、转台反馈、停止回执与DMA状态分别留档。毫米精度、全物体覆盖和弱线新算法不因本配置发布而通过验收。
