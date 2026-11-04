# 基于 Zynq 的激光三维扫描系统

2026 年 FPGA 创新设计赛道 AMD 自主选题初级组作品。系统采用 TUL PYNQ-Z2，核心器件为 AMD Zynq-7000 XC7Z020-1CLG400，搭配 USB 相机、线激光和电动转台。PL 以 50 MHz 提取激光条纹中心，PS 完成转台控制、采集、三角测量、点云拼接与形状拟合，电脑端接收和展示结果。

## 工程结构

```text
zynq-laser-3d-scanner/
├── README.md
├── src/       # RTL、PL 驱动、PS 扫描建模和电脑展示源码
├── sim/       # 测试台、参考模型、验证脚本和仿真结果
├── build/     # 构建脚本、约束、第三方预设和实现报告
├── board/     # Overlay、上板工具和真实板测输出
├── data/      # 测试向量、标定参数与扫描示例
├── skill/     # 可复用的验证方法
└── report/    # 设计报告、复现手册及大模型协作记录
```

## 下载后先做什么

使用 Python 3.10+，建议在短英文路径解压。运行下面的命令可准备运行目录并执行离线检查。

```sh
python -m pip install -r requirements.txt
python sim/run_checks.py
```

`sim/run_checks.py` 会先核对文件哈希，再执行独立参考、56 帧历史真实板测对照、Overlay 接口及形状拟合检查。

本次调整按功能归档文件。发布校验清单和代码中的原相对路径保留在源码内部，运行前执行 `python build/prepare_runtime.py`，即可在 `build/runtime/` 生成兼容的执行目录。该目录由源码和 `build/source_map.json` 生成。后续仿真、Vivado 构建及板端部署均从这个运行目录执行。仓库内受版本控制的路径采用英文命名，生成目录保留经过验证的原路径。准备脚本只处理本地文件。

完整操作见 [复现手册](report/reproduce.md)。

## 源码入口

| 功能 | 路径 |
| --- | --- |
| 激光条纹核心、AXI 接口 | [src/rtl](src/rtl/) |
| PL 驱动及静态板测 | [src/pl_driver](src/pl_driver/) |
| 最新整圈采集、按键与异常清理 | [src/ps/101_relocation_scan_20261102](src/ps/101_relocation_scan_20261102/) |
| 几何坐标恢复 | [src/ps/100_relocation_geometry_20261102](src/ps/100_relocation_geometry_20261102/) |
| 多形状拟合 | [src/ps/89_multi_shape_display_20261024](src/ps/89_multi_shape_display_20261024/) |
| 三棱锥拟合候选 | [src/ps/104_triangular_pyramid_display_20261103](src/ps/104_triangular_pyramid_display_20261103/) |
| 电脑网页和接收程序 | [src/pc](src/pc/) |

## 查看示例与已验证范围

- [六棱柱离线浏览页](data/examples/102_simple_object_test_20261102/result_be69ed433655/surface_proposal/model_viewer.html)可直接用浏览器打开。同目录保存估计网格和去噪点云，上级目录保存实测 CSV 和 PLY。
- [三棱锥历史点云重放](data/examples/104_triangular_pyramid_display_20261103/evidence/saved709_preview/model_viewer.html)展示另一轮 709 个实测点的离线拟合，当前三棱锥版本待部署和新一轮板测。
- PL 静态板测共 56 帧，每帧 640 列，有效位差异为 0，最大坐标差 0.00390625 像素。[真实板测输出与记录](board/evidence/)支持离线重新比对。
- 2026-11-02 六棱柱实测包含 36 个独立角度与闭环姿态、222 次 PL 提取，采集耗时 669.18 秒，得到 1,659 个实测点，去噪后 1,650 点。测点主要覆盖顶面，模型侧面、底面及缺测区域使用形状先验估计。独立尺寸精度和异机冷启动仍待验收。

[设计报告](report/design_report.pdf)与[大模型协作记录](report/ai_collaboration_log.md)随源码提供。第三方与自研部分授权状态见 [许可说明](licensing.md)。
