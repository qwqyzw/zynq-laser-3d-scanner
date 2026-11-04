# 上板工程与真实输出

`overlays/` 保存配对的 BIT/HWH，`tools/` 保存板端准备与 SSH 连接模板，`evidence/` 保存 56 帧真实板测输出及对照记录。

上板前先在仓库根目录运行 `python build/prepare_runtime.py`。整机代码保留原板端部署根目录约定，应把生成的 `build/runtime/` 内容部署到复现手册指定的项目目录。当前板端安装不自动切换到本次仓库布局。完整接线、配置与操作见 `report/reproduce.md`。
