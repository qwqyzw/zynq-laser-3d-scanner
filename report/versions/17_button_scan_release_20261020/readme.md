# PL＋转台UART＋板载按键版本

独立版本 `20261020.pl_uart_button.v1`，构建工程 `11_fpga_project/build/button2/ls.xpr`。原14联合发布、04运行源码及原mu2工程均不覆盖。

新增 `axi_gpio_scan`，地址 `0x41200000`：通道1为4位按钮输入，通道2为4位LED输出。BTN0=D19、BTN1=D20、BTN2=L20、BTN3=L19；LED0=R14、LED1=P14、LED2=N16、LED3=M14。按钮高有效，两级同步、8个ASYNC_REG触发器；软件另做防抖和1秒长按。

保留DMA `0x40400000`、UART `0x42C00000`、条纹核心 `0x43C00000`，FCLK0=50MHz。UART仍为Arduino D1/U12发送、D0/T14接收，9600 8N1。

Vivado 2026.1实现和bit生成完成：setup WNS=0.957ns、hold WHS=0.031ns，无失败端点，内部无无时钟/未约束端点；CDC报告4路按钮均识别为ASYNC_REG同步器。所有按钮/LED/UART引脚在实现IO报告中核对。报告保存在implementation。

构建警告原样保留：官方PS7预设的负DDR DQS skew提示与父版本一致；XDC中的数量检查`if`被Vivado提示不支持。实际异步例外已在`button_impl_exceptions.rpt`确认，仅按钮输入至第一级D，第二级不被豁免；实现后Tcl检查8个同步寄存器，CDC报告4路均通过识别。没有声称构建零警告。

SHA256.json是构建及软件身份清单，**不代表已经完成板上按键或物体精度验收**。后续板测单独留证，不通过更新旧指纹制造通过。当前功能使用明确标记的实验点云模式，轴拼接与毫米精度状态保持失败/未验收。操作见同根09_tools/README_button_scan.md。
