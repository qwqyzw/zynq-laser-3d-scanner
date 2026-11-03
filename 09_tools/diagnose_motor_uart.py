"""Bounded, one-request UART diagnosis; default only verifies project files.

--run-board --project ROOT --output NEW.json explicitly permits the fixed
read-only version request 01 1f 6b. No overlay, clock, UART control or FIFO-reset
register is written. Errors remain failures even if a later valid reply arrives.
"""
import argparse
from contextlib import ExitStack
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

from object_board_scan import assert_idle


VERSION_REQUEST = b'\x01\x1f\x6b'
RX_FIFO, TX_FIFO, STATUS = 0, 4, 8
RX_VALID, TX_EMPTY, TX_FULL, ERRORS = 1, 4, 8, 0xE0
PASSIVE_S, QUIET_S, ACTIVE_S, POLL_S = .100, .020, .250, .0005


def new_report():
    return {'schema_version': 1, 'status': 'failed', 'diagnostic_only': True,
            'hardware_accessed': False, 'uart_accessed': False,
            'request_hex': VERSION_REQUEST.hex(' '), 'motor_motion_sent': False,
            'overlay_downloaded': False, 'clock_changed': False,
            'uart_control_written': False, 'fifo_reset_sent': False,
            'automatic_recovery_performed': False, 'service_restart_performed': False,
            'status_trace': [], 'tx_fifo_writes': [], 'rx_bytes': [], 'raw_rx_hex': '',
            'sent_to_fifo_count': 0, 'first_tx_s': None,
            'first_error_s': None, 'first_error_relative_to_first_tx_s': None,
            'first_active_error_s': None, 'first_active_error_relative_to_first_tx_s': None,
            'tx_fifo_empty_observed_s': None, 'tx_fifo_empty_relative_to_first_tx_s': None,
            'tx_wire_idle_confirmed': False, 'recovery_required': False,
            'sampling_note': 'Software monotonic timestamps bracket MMIO operations; '
                             'TX_EMPTY indicates FIFO empty, not final wire stop-bit completion.',
            'timing': {'passive_s': PASSIVE_S, 'required_final_quiet_s': QUIET_S,
                       'active_s': ACTIVE_S, 'poll_s': POLL_S}}


def decode_version(raw):
    if len(raw) != 7 or raw[:2] != b'\x01\x1f' or raw[-1] != 0x6B:
        raise ValueError('Version reply must be exactly 01 1f <4 payload bytes> 6b')
    return {'firmware_version_code': int.from_bytes(raw[2:4], 'big'),
            'hardware_series_code': raw[4] >> 4, 'hardware_type_code': raw[4] & 15,
            'hardware_revision_code': raw[5]}


def sample_version(mmio, *, clock=time.monotonic, sleep=time.sleep, report=None):
    """Sample an injected UART MMIO; the only writes are one fixed version frame.

    Passive RX bytes are never read. Only an error on the very first passive
    status read can be labelled historical, and remains in the evidence.
    """
    report = new_report() if report is None else report
    report['uart_accessed'] = True
    origin = clock()
    raw = bytearray()
    last_status = None
    pending_write = False
    active_error = False

    def observe(phase):
        nonlocal last_status, active_error
        before = clock() - origin
        value = int(mmio.read(STATUS))
        after = clock() - origin
        row = {'phase': phase, 'before_s': before, 'after_s': after,
               'value': value, 'tx_empty': bool(value & TX_EMPTY),
               'tx_full': bool(value & TX_FULL), 'rx_valid': bool(value & RX_VALID),
               'errors': value & ERRORS}
        report['status_trace'].append(row)
        if len(report['status_trace']) > 4096:
            raise RuntimeError('Status trace limit exceeded')
        last_status = value
        if value & ERRORS:
            if report['first_error_s'] is None:
                report['first_error_s'] = before
            if phase == 'active':
                active_error = True
                if report['first_active_error_s'] is None:
                    report['first_active_error_s'] = before
        if value & ~0xFF:
            raise RuntimeError('Unexpected reserved UART status bits')
        return row

    try:
        report['phase'] = 'passive'
        passive_end = origin + PASSIVE_S
        clean_since = None
        reasons = set()
        first = True
        while True:
            row = observe('passive')
            value = row['value']
            if value & 3:
                reasons.add('existing_rx_bytes_not_read')
            if not value & TX_EMPTY or value & TX_FULL:
                reasons.add('existing_tx_not_empty_or_full')
            if value & ERRORS and not first:
                reasons.add('passive_error_after_initial_sample')
            if first:
                report['initial_read_clear_errors'] = value & ERRORS
            clean = not value & (ERRORS | 3 | TX_FULL) and bool(value & TX_EMPTY)
            clean_since = (row['before_s'] if clean_since is None else clean_since) if clean else None
            first = False
            if clock() >= passive_end:
                break
            sleep(min(POLL_S, max(0, passive_end - clock())))
        quiet = 0 if clean_since is None else clock() - origin - clean_since
        report['passive_final_quiet_s'] = quiet
        if quiet < QUIET_S:
            reasons.add('final_twenty_ms_not_clean')
        # Check once more immediately before any possible TX write.
        if not reasons:
            row = observe('pre_send')
            if row['value'] & (ERRORS | 3 | TX_FULL) or not row['tx_empty']:
                reasons.add('uart_changed_before_send')
        report['passive_refusal_reasons'] = sorted(reasons)
        if reasons:
            report['error'] = 'Passive observation refused the version request'
            return report
        report['initial_error_diagnostic_exception_used'] = bool(report['initial_read_clear_errors'])
        report['phase'] = 'active'
        report['first_tx_s'] = clock() - origin
        active_end = clock() + ACTIVE_S
        for value in VERSION_REQUEST:
            row = {'before_s': clock() - origin, 'value': value, 'write_returned': False}
            report['tx_fifo_writes'].append(row)
            pending_write = True
            mmio.write(TX_FIFO, value)
            row.update(after_s=clock() - origin, write_returned=True)
            report['sent_to_fifo_count'] += 1
        while True:
            row = observe('active')
            if row['tx_empty'] and report['tx_fifo_empty_observed_s'] is None:
                report['tx_fifo_empty_observed_s'] = row['before_s']
            if row['rx_valid']:
                before = clock() - origin
                value = int(mmio.read(RX_FIFO)) & 0xFF
                raw.append(value)
                report['rx_bytes'].append({'before_s': before, 'after_s': clock() - origin,
                                           'value': value})
                report['raw_rx_hex'] = raw.hex(' ')
                if len(raw) > 512:
                    raise RuntimeError('Receive evidence limit exceeded')
            if clock() >= active_end:
                break
            # Never resend after an error; keep observing this one request.
            if not row['rx_valid']:
                sleep(min(POLL_S, max(0, active_end - clock())))
        terminal = observe('active')
        report['active_terminal_rx_pending'] = bool(terminal['value'] & 3)
        report['phase'] = 'completed'
        if active_error:
            report['error'] = 'UART errors occurred during this request; later bytes do not clear failure'
        elif report['active_terminal_rx_pending']:
            report['error'] = 'Receive bytes still pending at the end of bounded capture'
        else:
            report['decoded_version'] = decode_version(bytes(raw))
            if report['first_error_s'] is None:
                report['status'] = 'passed'
            else:
                report['error'] = 'Initial read-clear error retained; valid diagnostic reply is not a clean preflight pass'
    except BaseException as error:
        report['status'] = 'failed'
        report['error'] = type(error).__name__ + ': ' + str(error)
        report['exception_phase'] = report.get('phase')
        # Capture the terminal queue state when possible, without another write.
        try:
            observe('exception_final')
        except BaseException as final_error:
            last_status = None
            report['final_status_error'] = type(final_error).__name__ + ': ' + str(final_error)
    finally:
        report['raw_rx_hex'] = raw.hex(' ')
        report['last_uart_status'] = last_status
        report['observation_elapsed_s'] = clock() - origin
        first_tx = report['first_tx_s']
        if first_tx is not None:
            for source, destination in (
                ('first_error_s', 'first_error_relative_to_first_tx_s'),
                ('first_active_error_s', 'first_active_error_relative_to_first_tx_s'),
                ('tx_fifo_empty_observed_s', 'tx_fifo_empty_relative_to_first_tx_s')):
                if report[source] is not None:
                    report[destination] = report[source] - first_tx
        # A failed/partial write has an unknown effect even if FIFO looks empty.
        incomplete = pending_write and (report['sent_to_fifo_count'] != 3 or
                                       any(not row['write_returned'] for row in report['tx_fifo_writes']))
        queued_or_unknown = last_status is None or not last_status & TX_EMPTY or bool(last_status & TX_FULL)
        if incomplete or queued_or_unknown:
            report.update(status='recovery_required', recovery_required=True,
                          service_restart_inhibited=True,
                          recovery_reason='TX queue state is pending/unknown or request write was incomplete; '
                                          'manual audited recovery required, never auto-restart service')
        else:
            report['service_restart_inhibited'] = False
    return report


def inspect_project(project):
    project = Path(project).resolve()
    if project != Path(__file__).resolve().parents[1]:
        raise ValueError('Run the diagnosis script from the selected project')
    sys.path.insert(0, str(project / '04_runtime'))
    runtime = importlib.import_module('pl_runtime')
    if Path(runtime.__file__).resolve() != project / '04_runtime/pl_runtime.py':
        raise ValueError('Runtime imported from another project')
    integrated = runtime.inspect_integrated_artifacts(project)['integrated_release']
    from button_overlay_runtime import inspect_button_release
    button = inspect_button_release(project, project / '17_button_scan_release_20261020')
    releases = []
    for label, entries, bit, hwh in (
        ('14', integrated, 'laser_motor.bit', 'laser_motor.hwh'),
        ('17', button, 'laser_motor_button.bit', 'laser_motor_button.hwh')):
        root = ET.parse(entries[hwh]['absolute_path']).getroot()
        modules = {item.get('INSTANCE'): item for item in root.findall('.//MODULE')}
        uart = modules['axi_uartlite_0']
        values = {item.get('NAME'): item.get('VALUE') for item in uart.findall('./PARAMETERS/PARAMETER')}
        for name, expected in (('C_S_AXI_ACLK_FREQ_HZ', 50000000), ('C_BAUDRATE', 9600),
                               ('C_DATA_BITS', 8), ('C_USE_PARITY', 0),
                               ('C_BASEADDR', 0x42C00000)):
            if int(values.get(name, '-1'), 0) != expected:
                raise ValueError('Unexpected UART HWH parameter: ' + name)
        releases.append({'release': label, 'bit': entries[bit], 'hwh': entries[hwh]})
    return {'project': str(project), 'releases': releases, 'hardware_accessed': False}


def assert_no_hardware_owners():
    camera = subprocess.run(['fuser', '/dev/video0'], capture_output=True, text=True, timeout=5)
    if camera.returncode != 1 or camera.stdout.strip():
        raise RuntimeError('Camera occupied or camera ownership cannot be checked')
    names = {'load_button_overlay.py', 'board_button_scan_service.py', 'object_board_scan.py',
             'fixed_exposure_worker.py', 'run_integrated_board.py', 'motor_live_test.py',
             'motor_readonly.py', 'diagnose_motor_uart.py', 'reconstruct_object_on_zynq.py'}
    active = []
    for process in Path('/proc').glob('[0-9]*'):
        if int(process.name) == os.getpid():
            continue
        try:
            args = process.joinpath('cmdline').read_bytes().decode('utf-8', 'replace').split('\0')
        except FileNotFoundError:
            continue
        if {Path(arg).name for arg in args}.intersection(names):
            active.append(int(process.name))
    if active:
        raise RuntimeError('Other hardware job exists: ' + str(active))


def run_board(inspection, report):
    from board_pointcloud_export import board_identity
    identity = board_identity()
    report['platform'] = identity
    if not identity.get('is_linux_arm_zynq_or_pynq'):
        raise RuntimeError('Physical Linux ARM Zynq/PYNQ is required')
    import fcntl
    os.environ['XILINX_XRT'] = '/usr'
    os.environ['BOARD'] = 'Pynq-Z2'
    from pynq import PL, Clocks, MMIO
    with ExitStack() as owners:
        for name in ('laser_motor_button_service.lock', 'laser_motor_object_scan.lock'):
            handle = owners.enter_context(open('/tmp/' + name, 'a'))
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert_no_hardware_owners()
        current = str(PL.bitfile_name)
        selected = [item for item in inspection['releases']
                    if Path(current).resolve() == Path(item['bit']['absolute_path']).resolve()]
        if len(selected) != 1 or Clocks.fclk0_mhz != 50.0:
            raise RuntimeError('Current overlay must match verified 14/17 with FCLK0=50MHz')
        report.update(selected_release=selected[0], bitfile=current, fclk0_mhz=Clocks.fclk0_mhz,
                      hardware_accessed=True, phase='core_dma_guard')
        core, dma = MMIO(0x43C00000, 0x10000), MMIO(0x40400000, 0x10000)
        snapshot = {'status': int(core.read(4)), 'completed_frames': int(core.read(0x38)),
                    'dma': {'mm2s_cr': int(dma.read(0)), 'mm2s_sr': int(dma.read(4)),
                            's2mm_cr': int(dma.read(0x30)), 's2mm_sr': int(dma.read(0x34)),
                            'received_bytes': int(dma.read(0x58))}}
        report['initial_idle_snapshot'] = snapshot
        report['core_id'], report['core_version'] = int(core.read(8)), int(core.read(12))
        if report['core_id'] != 0x4C535431 or report['core_version'] != 0x10000:
            raise RuntimeError('Unexpected stripe core ID/version')
        assert_idle(snapshot)
        return sample_version(MMIO(0x42C00000, 0x10000), report=report)


def save_report(path, report):
    temporary = path.with_name(path.name + '.writing')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    os.replace(temporary, path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--check-only', action='store_true')
    modes.add_argument('--run-board', action='store_true')
    parser.add_argument('--project', type=Path)
    parser.add_argument('--output', type=Path, help='New JSON evidence file; existing files are refused')
    args = parser.parse_args(argv)
    if args.run_board and (args.project is None or args.output is None):
        parser.error('--run-board requires explicit --project and --output')
    report = new_report()
    output = args.output.resolve() if args.output is not None else None
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        # Reserve before inspecting or touching hardware; never replace evidence.
        with output.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    try:
        report['phase'] = 'project_inspection'
        inspection = inspect_project(args.project or Path(__file__).resolve().parents[1])
        report['inspection'] = inspection
        if args.run_board:
            run_board(inspection, report)
        else:
            report.update(status='check_only_verified', phase='completed')
    except BaseException as error:
        report.update(status='failed', error=type(error).__name__ + ': ' + str(error),
                      exception_phase=report.get('phase'))
    finally:
        if output is not None:
            save_report(output, report)
    print(json.dumps({'status': report['status'], 'hardware_accessed': report['hardware_accessed'],
                      'sent_to_fifo_count': report['sent_to_fifo_count'],
                      'raw_rx_hex': report['raw_rx_hex'], 'error': report.get('error'),
                      'recovery_required': report['recovery_required'],
                      'output': str(output) if output is not None else None}, ensure_ascii=False))
    return 2 if report['recovery_required'] else 0 if report['status'] in ('passed', 'check_only_verified') else 1


if __name__ == '__main__':
    raise SystemExit(main())
