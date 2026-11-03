# 完整主工程中的PL开发模块

**当前使用已通过真实联动验证的UART版本。** 实际工程 `build/mu2/ls.xpr`，发布配对在同根 `14_integrated_release_20261020/laser_motor.bit` 与 `.hwh`。56次冻结回归及12次现拍提取、0°→5°→0°停稳采图均通过，见[完整报告](../15_integrated_test_20261020/修复与联动验证结果.md)。以下static工程与06发布的说明保留用于历史静态版本。

这是昨天 `laser_horizontal_v4` 的PL代码移植版，现增加独立的 `motor_uart` 硬件版本。算法核心、AXI封装和静态已验收bit/hwh保持原字节。整个完整交接包是依赖单位，不能只复制11目录而丢弃01或06。

## 目录与已有成果

- `rtl/laser_stripe_core.sv`：横向激光每列y中心提取，640×480输入。
- `rtl/laser_stripe_axi.sv`：AXI-Lite与32位流封装。
- `tb/`、`constraints/`：真实RTL测试台与核时钟约束。
- `scripts/`：便携构建、仿真和打包入口，优先用同根 `../01_fpga_baseline` 冻结数据。
- `third_party/pynq_z2/`：固定版本的官方PS预设、来源与许可证。
- `software/`：配套驱动与离线测试。demo/验证器在完整包布局中默认使用同根06发布；单独生成运行包时使用自身布局。
- `output/static/`：复制的同次构建bit/hwh，和同根06完全相同；不是本次重新综合生成的配置。
- `evidence_20261019/`：原仿真输出、时序/资源/DRC及旧阶段文档快照，路径和board_tested=false等字段是历史状态。
- `reports/`、`results/`、`build/`：本目录下新执行产生的工作结果。

## 电脑使用

在完整交接包根目录执行，`python`需能导入NumPy；离线采图回放/整机检查另需OpenCV。

```powershell
python 09_tools/verify_package.py
python 09_tools/check_pl_integration.py
python 11_fpga_project/scripts/project.py create
python 11_fpga_project/scripts/project.py sim-all
```

Vivado工具可从PATH、`VIVADO_BIN`或本机已安装目录找到。本机Vivado2026.1位于 `D:/AMDDesignTools/2026.1/Vivado/bin`。主设计为PYNQ-Z2的PS+Simple双向DMA+识别核，默认50MHz，主XPR为 `build/static/ls.xpr`。独立算法/AXI仿真工程由相应脚本创建；后续重新构建在新输出目录导出，不覆盖已经验证的bit/hwh。

原设计已完成布局布线：WNS+0.849ns，WHS+0.024ns，10083LUT、6107FF、122/140BRAM、6DSP。真实46组RTL输出和额外20组输出在历史evidence中。本次迁移是否重新运行仿真及结果以 `reports/migration_verification.json` 为准；复制旧报告不等于重新实现。

## 与整机程序衔接

同根 `04_runtime/pl_runtime.py` 提供只读 `inspect_artifacts()` 与显式 `load_static_runtime()`。调用示意（仅在已确认可替换当前配置、无在途DMA时明确执行）：

```python
import sys
from pathlib import Path
ROOT = Path('/home/xilinx/jupyter_notebooks/teammate_complete_20261020')
sys.path.insert(0, str(ROOT / '04_runtime'))
from pl_runtime import inspect_artifacts, load_static_runtime
info = inspect_artifacts()          # 只读文件，不访问硬件
# runtime = load_static_runtime()   # 明确调用才替换FPGA配置
# result = runtime.driver.run_bgr(image, config)
```

同一会话只创建一个runtime，之后复用其driver，保留runtime所有者。仍使用原驱动的flush/invalidate、超时、双向恢复和隔离规则；不能混用原生DMA对象。04运行副本、06冻结发布、11开发驱动的指纹必须一致，检查器会防止混用；未来修改驱动或硬件ABI需明确版本并同步受影响发布。

旧静态版本用 `06_fpga_verified/software/verify_board.py`，先 `--check-only`；`--run-board` 会加载06并运行56帧。12目录记录此前静态板测。联合版本的实际测试使用 `09_tools/run_integrated_board.py`，结果在15目录；直接运行该文件仅检查14发布。其 `--run-board` 流程要求初始为停止且无在途DMA的已知base恢复配置，并会加载14、运行回归及小角度运动；刚完成本轮测试的板卡已停留在14，不能直接重复该初始状态流程。

当前联合运行API是 `load_integrated_runtime()`，之后复用其overlay和driver调用 `rig_workflow.run_small_scan`。加载要求转台已停止、旧DMA已空闲；只创建一个runtime并始终保留其所有者。运行参数和异常恢复要求见同根04说明。

输入保持307200个32位0x00RRGGBB，输出640个32位valid/Q8；地址为核0x43C00000、DMA0x40400000，以配套HWH为准。PS三帧质量审查仍在04运行库中，不属于PL核心。

## 后续范围

当前保持旧强线算法，尚未实现02弱线候选。静态设计仍无iop_arduino，原整机入口在缺UART时会提前拒绝运动。新增UART版本和静态版本分别构建；弱线、性能与几何精度仍需分别验证。

## 新增转台UART版本

`scripts/build_motor_uart_overlay.tcl` 在同一50MHz设计中增加标准AMD AXI UARTLite v2.0，原PS7、DMA、识别核参数不变。UART固定9600波特、8位数据、无校验、1位停止位；地址 `0x42C00000`，HWH中的IP名为 `axi_uartlite_0`。通过ARM端MMIO轮询访问，不包含MicroBlaze或iop_arduino。

板端TX使用Arduino D1（U12），RX使用Arduino D0（T14），均为LVCMOS33。引脚来自固定版本官方PYNQ-Z2 base约束，原文件和下载指纹在 `third_party/pynq_z2`。约束 `constraints/motor_uart.xdc` 仅排除异步UART板外边界路径；全部内部50MHz路径仍做时序检查。

```powershell
python 11_fpga_project/scripts/project.py bit --variant motor_uart
```

默认工程为 `build/motor_uart/ls.xpr`，导出 `output/motor_uart/laser_motor_uart.bit` 和同名 `.hwh`。本轮第一次长任务进程中断，旧日志保存在 `reports/motor_uart_interrupted_20261020_1942`；随后实际构建改用 `--build-dir build/mu2 --output-dir output/motor_uart_v2`。以 `reports/motor_uart_v2_completion.json` 的实际完成状态为准。

已有输出不会覆盖；重建时指定新的 `--build-dir` 与 `--output-dir`。Windows持久后台入口为 `scripts/run_motor_uart_build.ps1`，串行执行构建、路由引脚核验、产物指纹检查，并写最终状态，避免依赖交互工具会话存续。静态发布仍在06与 `output/static`。完整新发布与板测结论由根目录的后续版本记录，不以成功生成bit代替上板验收。

`reports/motor_uart` 保存本轮综合、布局布线、时序、DRC和地址报告。完成构建后，`scripts/audit_motor_uart_overlay.tcl` 可独立打开路由检查点核对实际引脚、电平、时钟和DRC。官方DDR预设沿用旧版16-bit配置，构建阶段PSU-1/PSU-2关于lane2/3负skew值的提示保留在原始日志中；应区分这些预设提示与最终实现DRC统计。

2026-10-20 19:58，本轮 `build/mu2` 已完成构建、路由引脚审计和26项产物检查：50MHz下WNS +0.700ns、WHS +0.018ns；10190 LUT、6218 FF、122 BRAM、6 DSP；最终DRC为0错误、0严重警告、29警告、2建议。仅UART板外RX/TX路径按异步信号排除，内部未约束端点为0。完整指纹和参数比较见 `reports/motor_uart/motor_uart_build_verification.json`，板测结论另由整机记录给出。
