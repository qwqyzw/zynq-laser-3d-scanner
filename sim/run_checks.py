"""Run the documented offline checks against the validated execution layout."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'build'))
from prepare_runtime import prepare


def main():
    runtime, count = prepare()
    print(f'Checked {count} source files', flush=True)
    scripts = [
        '01_fpga_baseline/tools/test_comparator.py',
        'tools/compare_board_capture.py',
        '09_tools/check_button_overlay_runtime.py',
        '89_multi_shape_display_20261024/test_shapes.py',
        '104_triangular_pyramid_display_20261103/test_pyramid.py',
    ]
    for script in scripts:
        print(f'Running {script}', flush=True)
        subprocess.run([sys.executable, str(runtime / script)], cwd=runtime, check=True)


if __name__ == '__main__':
    main()
