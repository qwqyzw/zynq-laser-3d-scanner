# Overlay 与 DMA 检查清单

适用于 PYNQ 自定义 PL 加速器的首次联调和版本升级。换板卡时先用该板卡的 PS 时钟、DDR、DMA 端口和 Overlay 元数据，重新建立下面的契约。

1. 用新目录生成同名 `.bit`、`.hwh`，登记哈希和 RTL 版本。下载前比对目标板和同次构建配对，拒绝混用发布版本。
2. 从 HWH 或 `overlay.ip_dict` 核对 IP 名、地址、窗口、时钟和 DMA 模式。把起始寄存器、状态、数据单位与完成标志写进接口契约。PYNQ-Z2 示例在 [接口契约](../01_fpga_baseline/contract.json) 和 [实现摘要](../17_button_scan_release_20261020/implementation/button_design_summary.txt)。
3. 在静止且无在途 DMA 时测试最小冻结帧。确认连续物理内存、对齐、缓存同步和先准备接收通道；等待发送、接收和 IP 独立完成证据，核对输出字节数及帧结束。参考 [示例驱动](../11_fpga_project/software/laser_stripe.py)，移植时将格式和传输参数换成自己的。
4. 超时记录状态、错误位、计数器和接收长度；不能确认硬件不再访问内存时保留缓冲区，不要释放后继续扫描。恢复应按平台规范判断；不要无条件重试。
5. 为配对错、HWH 地址错、文件被修改、重复或越界路径写反例。项目命令：`python 09_tools/check_button_overlay_runtime.py`。

边界：该项目用 Simple DMA、32 位流、无 DRE。支持 scatter-gather、缓存一致性端口或其他位宽的设计应改写第 3、4 步。离线反例只能验证检查器，实际握手需上板留证。
