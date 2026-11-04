# PL 工程

- `rtl/laser_stripe_core.sv`：640×480 输入，横向激光每列 Y 中心提取。
- `rtl/laser_stripe_axi.sv`：AXI-Lite 控制及 32 位流封装。
- `tb/`：条纹、AXI 和协议测试台；输入与独立黄金参考见同根 `01_fpga_baseline/`。
- `constraints/`：核心与 UART 约束；按钮约束见同根 17 发布的 `source/`。
- `software/`：PYNQ 驱动、离线单元测试及静态帧演示。
- `third_party/pynq_z2/`：固定版官方 PS7 预设、约束来源和许可证。

具体构建、仿真和板上静态复现命令见[复现手册](../reproduce.md)；原始时序与资源报告见 [UART 版本](../14_integrated_release_20261020/implementation/)及 [UART＋按钮版本](../17_button_scan_release_20261020/implementation/)。

整机采集选用 17 发布同次构建的 BIT/HWH，配合 101 板端采集。独立的 06 静态发布可用于不含运动控制的冻结帧对照。不能混配 BIT 与 HWH 或把静态工程的通过记录充当整机新版本验收。

输入是 307,200 个 32 位 0x00RRGGBB，输出为 640 个 32 位 valid/Q8 坐标词；DMA 基址 0x40400000，条纹核基址 0x43c00000。实测值和参考文件位置见 [验证索引](../evidence/)。
