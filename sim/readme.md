# 仿真与离线验证

在仓库根目录运行 `python sim/run_checks.py`。RTL 仿真先运行 `python build/prepare_runtime.py`，再进入 `build/runtime` 执行 `python 11_fpga_project/scripts/project.py sim-all`。需要 Vivado 2026.1。`tb/` 保存测试台，`reference/` 保存独立参考，`tests/` 保存版本回归用例，`tools/` 保存板测离线比较，`evidence/` 保存原仿真结果。
