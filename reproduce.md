# 复现手册

## 1. 电脑离线复现

Python 3.10+，推荐虚拟环境；仿真/综合安装 Vivado 2026.1 及 Zynq-7000 器件支持。将 `vivado` 放到 PATH，或为 Python 仿真入口设置 `VIVADO_BIN` 到 Vivado 的 bin 目录。在仓库根目录执行：

```sh
python -m pip install -r requirements-review.txt
python 01_fpga_baseline/tools/test_comparator.py
python tools/compare_board_capture.py
python 09_tools/check_button_overlay_runtime.py
python 89_multi_shape_display_20261024/test_shapes.py
python 104_triangular_pyramid_display_20261103/test_pyramid.py
python 11_fpga_project/scripts/project.py sim-all
```

`sim-all` 编译和仿真实际 SystemVerilog：46 项冻结参考比对、2 项 AXI 输出比对、协议测试、20 项定向比对。查看 `11_fpga_project/results/comparison/summary.json`、`11_fpga_project/reports/axi_comparison.json`、`11_fpga_project/reports/extra_comparison.json`。项目只存 HEX 输入，板测所需 BIN 可由步骤 3 转换；不使用要求原始交接全量图片和二进制副本的历史包检查命令。

离线展示时直接下载并用浏览器打开 `102_simple_object_test_20261102/result_be69ed433655/surface_proposal/model_viewer.html`，或用 CloudCompare 打开同目录的估计表面 PLY、与上级目录实测 `points.ply` 比较。三棱锥离线结果使用另一轮 709 个历史实测点，不与六棱柱数据混合。

## 2. 生成 Overlay

在短 ASCII 路径下的临时克隆执行。构建脚本在临时克隆里更新 `17_button_scan_release_20261020/implementation/`；验收证据克隆保持原状。运行：

```sh
vivado -mode batch -source 09_tools/build_button_overlay.tcl -tclargs bit build/button build/overlay
```

得到同名 `laser_motor_button.bit` / `laser_motor_button.hwh` 和综合、布局布线报告。目标器件 `xc7z020clg400-1`；FCLK0 50 MHz；PS7 预设来自 [固定版本的官方 PYNQ-Z2 设置](11_fpga_project/third_party/pynq_z2/README.md)。预生成发布的实际报告见 `17_button_scan_release_20261020/implementation/`。新构建的文件不要覆盖冻结版：整机软件有严格的产物哈希校验，移植时应另做版本并重新验证。

## 3. 最小上板验证

使用 PYNQ-Z2 和 PYNQ 3.x；已留证的静态环境为 PYNQ 3.1.1 / Linux armv7l。以有权限访问 FPGA 的 PYNQ Python 环境运行。板处于独立测试状态，未运行扫描服务、DMA 已空闲、转台和激光未接入或处于安全停机。以下第一步只准备输入，第二步只核验文件，第三步会实际配置 PL；它不需要相机或转台。

```sh
python tools/prepare_board_vectors.py build/review_handoff
python 11_fpga_project/software/static_image_demo.py --handoff build/review_handoff --sample flat_zero.png --mode full --dry-run
python 11_fpga_project/software/static_image_demo.py --handoff build/review_handoff --sample flat_zero.png --mode full --bit 06_fpga_verified/laser_static.bit --output-dir build/board_check
```

参照输出：307,200 个 32 位 RGB 打包词，640 个定点输出词；`flat_zero/full` 的冻结参考有效列数为 200，比较限值为 0.25 像素且有效位须完全一致。该流程另存真实 HEX 及 JSON，完整 56 帧历史实测的比较在第 1 步完成。

## 4. 整机版本和启动顺序

此流程只适用于已完成接线检查、相机/激光/转台几何标定、绝对位置与清障验证的设备。本仓库保存验证过的历史配置样例；换装置应先重新标定，不能直接套用标定参数或把旧安全许可当成新装置确认。

- PYNQ-Z2 接 USB 相机；UARTLite 9600 8N1、3.3 V，Arduino D1/U12 为 TX、D0/T14 为 RX，与兼容的控制接口交叉并共地；外接执行器使用自身匹配电源。PL 数据通路为 AXI DMA `0x40400000`、条纹核 `0x43c00000`；UART `0x42c00000`，GPIO `0x41200000`。
- 101 已验证服务仍有固定部署路径：项目位于 `/home/xilinx/jupyter_notebooks/integrated_uart_20261020_193135/teammate_complete_20261020`；配置及结果根目录为 `/home/xilinx/jupyter_notebooks/button_scan`。PC 网页脚本也通过此路径读取板端数据。若更改部署路径，须同步移植这些硬编码引用并重新做板测。
- 在板端把 `101_relocation_scan_20261102/config_relocation.example.json` 复制到结果根目录，命名 `config_relocation_20261102.json`。里面引用 `61_current_axis_laser_release_20261024/current_geometry.json` 是原运行装配校验；实际 101 扫描会另外检查并使用 `100_relocation_geometry_20261102/current_geometry.json`。两者用途不同，不直接替换模板里的 61 路径。
- 在板端仓库根目录，先以 PYNQ Python 运行下面的无硬件检查。只有独立硬件检查通过、现有服务已正常退出、无采集所有者时，才在同一条命令追加 `--load-board --serve-board`。

```sh
python 101_relocation_scan_20261102/service91.py --config /home/xilinx/jupyter_notebooks/button_scan/config_relocation_20261102.json --dual-manifest-sha256 ea4e1aea382a1b976719c18c3afa1001459a12674c5b2d3dc01acf174f77f571
```

- 要复现 89 实时模型，在第二个板端终端启动下列文件处理服务。网页的形状选择会写入该服务读取的偏好；板端服务账号须可读扫描结果并可写本地模型目录。若只检查点云，此服务可以不运行。

```sh
python 101_relocation_scan_20261102/model_compat.py --capture-manifest-sha256 ea4e1aea382a1b976719c18c3afa1001459a12674c5b2d3dc01acf174f77f571 --manifest-sha256 e32edb131598d2be52a1d60bd8ed4ea78f4fa5037dc17ee64fb394ab6147ff7c
```

- 确认网页形状选项和转台清障，长按 BTN0 约 2 秒后松开启动；BTN1 请求终止该轮，等待状态确认完成。板端输出在结果根目录的 `runs/<run_id>/model/viewer`。未部署实时展示时，可复制 `points.csv` / PLY 到电脑离线查看。
- 实时电脑端需要额外安装 Paramiko、配置 SSH 密钥/代理及已核验的主机密钥。可用 `61_current_axis_laser_release_20261024/pc_receiver_cancel.py --host <板卡地址> --display none` 接收。原 89 展示脚本另需将 `tools/board_ops.example.py` 复制为本机 `results/button_scan_20261020/board_ops.py`，设置 `ZYNQ_SSH_HOST`，运行 `python 89_multi_shape_display_20261024/pc_dashboard.py` 并访问 `http://127.0.0.1:8891/`。本机连接文件不入库。
- 101 是六棱柱实测所用采集版本；104 仅是离线三棱锥候选。对新装置的冷启动、毫米尺寸精度和 104 上板验收均没有现成通过记录。

## 5. 取舍及优化证据

| 实现 | PL 时钟 | LUT | FF | BRAM tile | DSP | 布线后 WNS |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 14：原条纹＋转台 UART | 50 MHz | 10,190 | 6,218 | 122 | 6 | +0.700 ns |
| 17：加入按键同步和 GPIO | 50 MHz | 10,252 | 6,308 | 122 | 6 | +0.957 ns |

新增按键逻辑带来 62 LUT 和 90 FF，BRAM/DSP 不变；这不是条纹提取加速对比。板端软件的两轮[文件级启动检查基准](66_responsive_button_start_20261024/zynq_file_tests_and_benchmark.json)为 4.746/4.767 秒降到 1.584/1.583 秒。该计时仅覆盖启动检查，并不表示整圈扫描缩短同样幅度。BRAM 占用 122/140 是当前硬件资源约束，未把搭配按键的 WNS 变化描述成更高的实际运行频率。
