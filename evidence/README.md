# 验证索引

| 检查 | 文件 | 结论和范围 |
| --- | --- | --- |
| 2026-11-03 Vivado 2026.1 RTL 仿真 | [冻结向量 46 项](rtl_sim_46.json)、[AXI 2 项](axi_sim_2.json)、[定向 20 项](rtl_directed_sim_20.json) | 本仓库运行 `project.py sim-all`，包括独立协议测试；全部通过。相对路径定位对应测试名称，报告中的绝对路径是该次运行产生的记录。 |
| 2026-10-20 PYNQ-Z2 板测 | [真实输出和逐列明细](static_board_56_frames.zip)、[原始汇总](static_board_summary.json)、[本轮离线复核](static_board_comparison.json) | 46 个冻结用例及 10 次重复帧，56 帧共 35,840 条列记录。有效标志零差异，最大共同有效坐标差 0.00390625 像素。板卡 RTC 未设置，档案内部 2025 日期不是实际测试日。 |
| 2026-10-20 Vivado 综合及实现 | [UART 版本](../14_integrated_release_20261020/implementation/)、[UART＋按钮版本](../17_button_scan_release_20261020/implementation/) | 同一 PYNQ-Z2 与 50 MHz PL 时钟。保留综合、布局布线、时序与资源结果。 |
| 2026-11-02 六棱柱整机实测 | [实测摘要](hex_prism_run_summary.json)、[点云和可视化](../102_simple_object_test_20261102/result_be69ed433655/) | 36 个独立角度，另有一闭环姿态；222 次 PL 提取，669.18 秒采集程序记录，1,659 点。网格的缺测面由形状先验补全。 |
| 2026-11-03 三棱锥离线预览 | [历史实测点云回放](../104_triangular_pyramid_display_20261103/evidence/saved709_preview/model_viewer.html) | 离线拟合，尚待部署和新板测。 |

重新比对板测实际值与独立冻结参考：

```sh
python tools/compare_board_capture.py --report board_comparison.json
```

脚本核对归档 SHA-256、用例顺序与每帧 640 列格式，再用参考 `expected/` 检查有效位和坐标。该命令不访问板卡。整机闭合 p95、条纹误差和物体绝对尺寸误差是不同指标；本项目未完成独立尺寸精度验收。
