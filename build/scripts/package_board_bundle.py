"""Build a self-contained runtime ZIP from this complete-handoff project.

The generated runtime has its own handoff subset; development uses the sibling
01_fpga_baseline directly. Existing releases are never overwritten.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from check_project import inspect_pair
from project_paths import ROOT, baseline_root, docs_root


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(friend=False):
    parser = argparse.ArgumentParser(description=__doc__)
    default_name = ("fpga_friend_verify_" if friend else "laser_static_board_") + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    parser.add_argument("--name", default=default_name, help="new release directory/ZIP name")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.name):
        parser.error("--name must use ASCII letters, numbers, underscores or hyphens")
    output = ROOT / "output"
    output.mkdir(parents=True, exist_ok=True)
    final, archive = output / args.name, output / (args.name + ".zip")
    if final.exists() or archive.exists():
        raise SystemExit("Refusing to overwrite release " + args.name)
    pair = inspect_pair(output / "static")
    baseline = baseline_root()
    samples = json.loads((baseline / "manifest.json").read_text(encoding="utf-8"))["samples"]
    stage = Path(tempfile.mkdtemp(prefix="package_", dir=output))
    for directory in ("software", "handoff/vectors", "handoff/expected", "docs"):
        (stage / directory).mkdir(parents=True, exist_ok=True)
    for name in ("laser_static.bit", "laser_static.hwh", "build_manifest.json"):
        shutil.copy2(output / "static" / name, stage / name)
    for name in ("laser_stripe.py", "static_image_demo.py", "register_map.json", "verify_board.py"):
        shutil.copy2(ROOT / "software" / name, stage / "software" / name)
    shutil.copy2(baseline / "manifest.json", stage / "handoff/manifest.json")
    for sample in samples:
        stem = Path(sample["name"]).stem
        shutil.copy2(baseline / "vectors" / (stem + "_input.bin"), stage / "handoff/vectors" / (stem + "_input.bin"))
        for mode in ("core", "full"):
            name = stem + "_" + mode + ".hex"
            shutil.copy2(baseline / "expected" / name, stage / "handoff/expected" / name)
    historical = stage / "docs/historical_20261019"
    historical.mkdir()
    for name in ("寄存器表.md", "Python驱动说明.md", "静态DMA工程.md"):
        shutil.copy2(docs_root() / name, historical / name)
    guide = """# PYNQ-Z2 静态图片验证包

开发源工程现在位于完整交接包的 `11_fpga_project`。本包是可以独立上传的运行资料。
bit/hwh 是配对的既有静态配置；其指纹在 `build_manifest.json` 中。
历史验证记录保存在开发主工程；本次打包只执行离线完整性检查，不代表再次上板。

在板卡的 PYNQ Python 环境中，进入本包目录：

```bash
python3 software/verify_board.py --check-only
python3 software/verify_board.py --run-board --timeout 10 --repeats 10
```

第二条命令会替换当前 PL 配置。确认转台静止、原 FPGA 程序已停止后运行。
需要 PYNQ 与 NumPy，不需要相机、激光、转台或 Vivado。
正式运行 23 张冻结图 × core/full 两种模式，再做 10 帧交替，共 56 帧。
首次失败停止；不要修改 expected，也不要混换 bit/hwh。
如果出现 `RECOVERY HOLD`，保持 Python 和终端运行，按终端提示恢复；
DMA 未确认静止前不能释放缓冲区或强杀 Python。

回传 `results/RETURN_board_verify_*.zip`、终端完整输出，并填写 `docs/验证反馈模板.txt`。
本配置不包含转台 UART，不能用它运行整机转台控制。
`docs/historical_20261019/` 是原设计说明，日期和“待上板”文字是当时状态。
"""
    (stage / "README.md").write_text(guide, encoding="utf-8")
    (stage / "docs/运行说明.md").write_text(guide, encoding="utf-8")
    (stage / "docs/验证反馈模板.txt").write_text(
        "结论（通过/失败）：\n板卡及 PYNQ 版本：\n实际执行命令：\n"
        "通过帧数/失败样本：\n是否修改文件或混换 bit/hwh：\n是否重启及原因：\n"
        "RETURN ZIP 文件名：\n终端错误原文：\n", encoding="utf-8")
    write_json(stage / "PACKAGE_INFO.json", {"release": args.name, "purpose": "static_frozen_image_validation",
        "source_layout": "complete_handoff/11_fpga_project", "board": "PYNQ-Z2", "clock_hz": 50000000,
        "input_samples": len(samples), "frozen_configurations": 2 * len(samples), "alternating_frames": 10,
        "default_total_frames": 2 * len(samples) + 10, "motor_control_included": False,
        "packaging_checks_only": True, "board_executed_during_packaging": False, "pair": pair})
    entries = [{"path": path.relative_to(stage).as_posix(), "bytes": path.stat().st_size, "sha256": digest(path)}
               for path in sorted(stage.rglob("*")) if path.is_file()]
    write_json(stage / "SHA256.json", {"format_version": 1, "release": args.name, "files": entries})
    command = [sys.executable, "-B", "-X", "utf8", str(stage / "software/verify_board.py"), "--check-only"]
    check = subprocess.run(command, cwd=stage, capture_output=True, text=True, encoding="utf-8")
    (ROOT / "reports").mkdir(parents=True, exist_ok=True)
    (ROOT / "reports" / (args.name + "_check.log")).write_text(check.stdout + check.stderr, encoding="utf-8")
    if check.returncode:
        raise RuntimeError("Package check failed; stage retained at " + str(stage) + "\n" + check.stdout + check.stderr)
    # Only immutable manifest-listed files go into the ZIP, never check-only results.
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
        for relative in sorted([row["path"] for row in entries] + ["SHA256.json"]):
            zipped.write(stage / relative, args.name + "/" + relative)
    with zipfile.ZipFile(archive) as zipped:
        if zipped.testzip() is not None:
            raise RuntimeError("ZIP CRC failed")
    with tempfile.TemporaryDirectory(prefix="static_release_check_") as temporary:
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(temporary)
        relocated = Path(temporary) / args.name
        check = subprocess.run([sys.executable, "-B", "-X", "utf8", str(relocated / "software/verify_board.py"), "--check-only"],
                               cwd=temporary, capture_output=True, text=True, encoding="utf-8")
        if check.returncode:
            raise RuntimeError("Relocated ZIP failed check\n" + check.stdout + check.stderr)
    stage.rename(final)
    report = {"passed": True, "bundle": str(final), "archive": str(archive), "archive_bytes": archive.stat().st_size,
              "archive_sha256": digest(archive), "files": len(entries) + 1, "board_access": False,
              "package_check_only_passed": True, "extracted_relocation_check_passed": True}
    write_json(ROOT / "reports" / (args.name + "_package.json"), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
