# 基于 Zynq 的激光三维扫描系统

2026 年 FPGA 创新设计赛道 AMD 自主选题初级组。系统使用 PYNQ-Z2、相机、线激光和电动转台。FPGA 的 PL 端提取激光条纹中心，Zynq 的 PS 端负责转台控制、采集、三角测量、点云拼接与几何拟合，电脑端接收结果并通过网页展示。

## 离线查看与复验

- 六棱柱实测示例位于 `102_simple_object_test_20261102/`。用 CloudCompare 打开示例中的 PLY 查看拟合网格，导入同一轮的 `points.csv` 查看实测点云。
- 三棱锥离线预览位于 `104_triangular_pyramid_display_20261103/evidence/saved709_preview/model_viewer.html`。它使用历史实测点云，三棱锥拟合已做离线验证，尚待部署和新一轮板上验证。
- 三棱锥及其他形状的算法回归可在仓库根目录执行。建议使用 Python 3.10 或更新版本和独立虚拟环境。

```sh
python -m pip install numpy scipy
python 104_triangular_pyramid_display_20261103/test_pyramid.py
```

## 工程入口

| 内容 | 位置 |
| --- | --- |
| 条纹提取 RTL、AXI 接口、仿真和约束 | `11_fpga_project/` |
| PYNQ-Z2 按键、UART、DMA 与已构建的 Overlay | `17_button_scan_release_20261020/` |
| 标定与三维坐标恢复 | `100_relocation_geometry_20261102/` |
| 转台整圈采集与按钮服务 | `101_relocation_scan_20261102/` |
| 圆锥、六棱柱、正方体的拟合与展示 | `89_multi_shape_display_20261024/` |
| 三棱锥拟合、展示与回归测试 | `104_triangular_pyramid_display_20261103/` |

Vivado 2026.1 批处理构建命令。请在单独的临时克隆中执行，构建脚本也会更新 17 发布目录中的实现报告。

```sh
vivado -mode batch -source 09_tools/build_button_overlay.tcl -tclargs bit build/button build/overlay
```

## 实机操作与已验证范围

实机依赖 PYNQ 环境、板端服务、相机与转台及对应的几何标定。移动相机或激光后需核对标定。转台上物体放稳且旋转路径无碰撞缠线时，在已部署的网页选择形状，长按 BTN0 约两秒并松开启动。BTN1 取消当前轮。打开电脑端展示页可查看实测点云和估计网格。

2026-11-02 的六棱柱实测使用 36 个独立角度，另采闭环姿态，共完成 222 次 PL 条纹提取，耗时约 11 分 9 秒，得到 1,659 个实测点。测点主要覆盖顶面，竖直侧面与缺测区域由选定形状拟合得到，尺寸精度未验收。板端配置模板为 101_relocation_scan_20261102/config_relocation.example.json。电脑端可参考 tools/board_ops.example.py，用 SSH 密钥或代理和已验证的主机密钥配置连接。配置项需按实际板卡、相机和网络修改。Vivado 和 PYNQ 运行环境需自行安装。

