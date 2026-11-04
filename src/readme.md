# 设计源码

`rtl/` 为条纹提取和 AXI 接口，`pl_driver/` 为配套驱动，`ps/` 为扫描与建模，`pc/` 为电脑接收和网页。`releases/` 保存与冻结发布绑定的元数据。各版本通过原相对路径相互引用，执行前请在仓库根目录运行 `python build/prepare_runtime.py`。源码路径到运行路径的完整对应见 `build/source_map.json`。
