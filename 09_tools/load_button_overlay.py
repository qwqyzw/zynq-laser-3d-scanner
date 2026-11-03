"""Explicit commissioning/boot loader; inspecting this file never touches hardware.

--load-board is required for FPGA programming. Only an unconfigured PL or this
project's idle 14/17 overlay may be replaced. No enable, position, or stop motor
command is sent. The coordinator still requires a fresh physical button press.
"""
import argparse
from pathlib import Path
import os
import subprocess
import sys
import time

from board_button_scan_service import inspect_config, Coordinator, digest


def assert_quiescent(project, current, artifacts):
    owners = subprocess.run(['fuser', '/dev/video0'], capture_output=True, text=True, timeout=5)
    if owners.returncode != 1 or owners.stdout.strip():
        raise RuntimeError('Camera is occupied; refusing to load')
    active = []
    for entry in Path('/proc').glob('[0-9]*'):
        try:
            args = entry.joinpath('cmdline').read_bytes().decode('utf-8', 'replace').split('\0')
            names = {Path(value).name for value in args}
            if int(entry.name) != os.getpid() and '-c' not in args and names.intersection({
                'object_board_scan.py', 'fixed_exposure_worker.py', 'run_integrated_board.py',
                'board_button_scan_service.py', 'motor_live_test.py'}):
                active.append(int(entry.name))
        except (OSError, ValueError):
            pass
    if active:
        raise RuntimeError('Another hardware job exists; refusing to load')
    if current in ('None', ''):
        return {'previous_configuration': 'unconfigured_after_boot', 'registers_read': False}
    allowed = {Path(artifacts['integrated_release']['laser_motor.bit']['absolute_path']).resolve(),
               Path(artifacts['button_release']['laser_motor_button.bit']['absolute_path']).resolve()}
    if Path(current).resolve() not in allowed:
        raise RuntimeError('Unexpected configured overlay; refusing to replace it')
    from pynq import MMIO, Overlay
    from object_board_scan import assert_idle
    core, dma = MMIO(0x43C00000, 0x10000), MMIO(0x40400000, 0x10000)
    snapshot = {'status': int(core.read(4)), 'completed_frames': int(core.read(0x38)),
                'dma': {'mm2s_cr': int(dma.read(0)), 'mm2s_sr': int(dma.read(4)),
                        's2mm_cr': int(dma.read(0x30)), 's2mm_sr': int(dma.read(0x34)),
                        'received_bytes': int(dma.read(0x58))}}
    assert_idle(snapshot)
    selected = artifacts['button_release']['laser_motor_button.bit']
    if Path(current).resolve() == Path(selected['absolute_path']).resolve():
        if digest(current) != selected['sha256']:
            raise RuntimeError('Loaded button overlay file differs from selected release')
        # Reattaching the same configuration does not interrupt its UART or
        # change motor control. Keep all ownership/idle checks above; motion
        # still requires the scan child's independent UART preflight.
        return {'previous_configuration': current, 'idle_diagnostics': snapshot,
                'motor_uart_check': 'not_required_without_reprogramming'}
    sys.path.insert(0, str(project / '04_runtime'))
    from motor_uartlite import UARTLiteReadOnlyClient
    client = UARTLiteReadOnlyClient(Overlay(current, download=False))
    try:
        family = client.query('configuration')['firmware_family_layout']
        positions = []
        for _ in range(3):
            status = client.query('status')
            positions.append(client.query('position', firmware_family=family)['motor_position_deg'])
            if not status['position_reached'] or status['stall_detected'] or status['stall_protection_triggered']:
                raise RuntimeError('Motor is not stopped without fault')
            time.sleep(.15)
        if max(positions) - min(positions) > .1:
            raise RuntimeError('Motor position is changing')
    finally:
        client.close()
    return {'previous_configuration': current, 'idle_diagnostics': snapshot, 'motor_positions': positions}


def load_board(config, artifacts):
    from board_pointcloud_export import board_identity
    identity = board_identity()
    if not identity.get('is_linux_arm_zynq_or_pynq'):
        raise RuntimeError('Physical Linux ARM Zynq is required')
    import fcntl
    os.environ['XILINX_XRT'] = '/usr'
    os.environ['BOARD'] = 'Pynq-Z2'
    from pynq import PL, Overlay, Clocks, MMIO
    with open('/tmp/laser_motor_button_service.lock', 'a') as service_lock, \
            open('/tmp/laser_motor_object_scan.lock', 'a') as scan_lock:
        fcntl.flock(service_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(scan_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = assert_quiescent(config['project'], str(PL.bitfile_name), artifacts)
        expected = artifacts['button_release']['laser_motor_button.bit']['absolute_path']
        downloaded = Path(str(PL.bitfile_name)).resolve() != Path(expected).resolve()
        if downloaded:
            Overlay(expected)
        if Path(str(PL.bitfile_name)).resolve() != Path(expected).resolve() or Clocks.fclk0_mhz != 50.0:
            raise RuntimeError('Button overlay identity/clock verification failed')
        core = MMIO(0x43C00000, 0x10000)
        if core.read(8) != 0x4C535431 or core.read(12) != 0x10000 or core.read(4) & 9:
            raise RuntimeError('Button overlay core ID/version/idle check failed')
        uart_startup = []
        if downloaded:
            # A configuration transition may latch receive status before any
            # request exists. Observe the read-to-clear status, never reset or
            # drain FIFOs and never truncate a queued command. Require three
            # subsequent quiet/empty samples and retain every observation.
            uart = MMIO(0x42C00000, 0x10000)
            for _ in range(4):
                uart_startup.append(int(uart.read(8)))
                time.sleep(.05)
            if any(value & 0xE1 or not value & 4 for value in uart_startup[1:]):
                raise RuntimeError('New UART did not settle empty without receive errors; no motor command sent')
        return {'status': 'button_overlay_loaded' if downloaded else 'button_overlay_already_loaded',
                'bitfile': expected, 'fclk0_mhz': Clocks.fclk0_mhz, 'motor_motion_sent': False,
                'previous': previous, 'uart_startup_status': uart_startup, 'platform': identity}


def main():
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--load-board', action='store_true')
    parser.add_argument('--serve-board', action='store_true')
    args = parser.parse_args()
    config, artifacts = inspect_config(args.config)
    config['config_file'] = args.config.resolve()
    if args.load_board:
        print(json.dumps(load_board(config, artifacts), ensure_ascii=False), flush=True)
    elif not args.serve_board:
        print(json.dumps({'status': 'button_boot_configuration_verified', 'hardware_accessed': False}))
    if args.serve_board:
        Coordinator(config, artifacts).serve()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Button startup rejected: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
