# 构建

`prepare_runtime.py` 依据 `source_map.json` 核对并恢复经过验证的执行布局。`scripts/`、`constraints/` 和 `third_party/` 保存 Vivado 构建所需来源，`reports/` 保存原综合及实现报告。生成的 `runtime/` 由 Git 忽略。

在仓库根目录执行 `python build/prepare_runtime.py`，进入 `build/runtime` 后运行 `vivado -mode batch -source 09_tools/build_button_overlay.tcl -tclargs bit build/button build/overlay`。构建会更新生成目录中的报告。
