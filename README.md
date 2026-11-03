# 基于 Zynq 的激光三维扫描系统

2026 年 FPGA 创新设计赛道 AMD 自主选题。系统采用 **TUL PYNQ-Z2，AMD Zynq-7000 XC7Z020-1CLG400**，搭配相机、线激光和电动转台。PL 以 50 MHz 提取激光条纹中心；PS 控制转台、采集、三角测量、拼接与几何拟合；电脑接收点云和模型并展示。

## 快速复现

建议使用不含空格和非 ASCII 字符的短目录，安装 Python 3.10+。打开[六棱柱实测浏览页](102_simple_object_test_20261102/result_be69ed433655/surface_proposal/model_viewer.html)可离线查看点云与估计表面，无需板卡。运行下面的两条命令可复核真实板测归档：

```sh
python -m pip install -r requirements-review.txt
python tools/compare_board_capture.py
```

完整的[复现手册](reproduce.md)包含 RTL 仿真、Vivado 2026.1 重建、板端静态测试及整机操作。若验证另一块板卡，按其硬件重新检查供电、电平、连接和标定。

## 目录

| 内容 | 位置 |
| --- | --- |
| 输入、独立参考、接口契约和比对程序 | [01_fpga_baseline](01_fpga_baseline/) |
| PS 通用运行库 | [04_runtime](04_runtime/runtime_entry.md) |
| PL 的 RTL、测试台、约束、构建脚本和驱动 | [11_fpga_project](11_fpga_project/) |
| UART 和按键版本构建与发布；复现入口 | [14_integrated_release](14_integrated_release_20261020/)、[17_button_scan_release](17_button_scan_release_20261020/)、[09_tools](09_tools/) |
| 搬运后标定、实测版采集和按键服务 | [100_relocation_geometry](100_relocation_geometry_20261102/)、[101_relocation_scan](101_relocation_scan_20261102/) |
| 当前六棱柱验证所用展示、样例 | [89_multi_shape_display](89_multi_shape_display_20261024/)、[102_simple_object_test](102_simple_object_test_20261102/) |
| 三棱锥离线候选 | [104_triangular_pyramid_display](104_triangular_pyramid_display_20261103/) |
| 仿真、板上输出与参考、整机证据及范围 | [evidence](evidence/) |
| 可复用 PYNQ 技能 | [skill](skill/) |
| 设计报告、大模型协作节录 | [report](report/) |

其他带日期或版本号的目录主要保存冻结发布所依赖的历史代码、校验清单或复验记录，不应任意重命名或改写。当前实现和板测证据的对应关系见复现手册。路径沿用既有工程组织，不强制搬移到指南的示例目录。

[设计报告](report/design_report.pdf)已于 2026 年 11 月 3 日按本次用户提供的官方技术报告模板重排为 15 页。已补入正面、斜俯视整机与独立板卡原照，原图及视角说明见[报告说明](report/README.md)。参赛承诺书由全体队员签署后在比赛系统单独提交，不进入公开仓库。

## 验证范围

- PL 静态板测：56 帧，640 列/帧；有效标志无差异，最大坐标差 0.00390625 像素。真实输出可和冻结参考重新比对。
- 2026-11-02 六棱柱整机实测：36 个独立角度加一闭环姿态，222 次 PL 提取，1,659 个实测点，去噪后 1,650 点。669.18 秒是该轮采集程序记录。测点主要覆盖顶面，侧面和底面是形状先验补全；独立尺寸精度尚未验收。
- 三棱锥支持用历史实测点云离线复验，尚待部署和新一轮板测。

第三方文件和自研部分的授权状态见 [许可说明](licensing.md)。
