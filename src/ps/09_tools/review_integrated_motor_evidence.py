"""Independently audit downloaded PL+UART test records; never access hardware."""
import argparse
import ast
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
QUERY = {'version': (0x1F, 7), 'voltage': (0x24, 5), 'encoder': (0x31, 5),
         'position': (0x36, 8), 'status': (0x3A, 4), 'configuration': (0x42, 33)}


def register_value(value):
    """Accept recorded integer registers and explicitly prefixed hex strings."""
    if type(value) is int:
        return value
    if isinstance(value, str):
        return int(value, 16 if value.lower().startswith('0x') else 10)
    raise ValueError('Register is not an integer or explicit numeric string')


def audit_diagnostics(diagnostics, check, expected_completed=None, *, check_quarantine=True):
    """Independently inspect raw core/DMA bits, not only driver health flags."""
    try:
        core = register_value(diagnostics['status'])
        check(bool(core & 2) and not core & 0xD, 'core DONE and no BUSY/ERROR/RESETTING')
        if check_quarantine:
            check(diagnostics['quarantined_buffers'] == 0, 'diagnostics has no DMA buffer quarantine')
        completed = register_value(diagnostics['completed_frames'])
        check(completed > 0, 'core completed-frame counter is nonzero')
        if expected_completed is not None:
            check(completed == expected_completed, 'core completed-frame counter matches all recorded frames')
        dma = diagnostics['dma']
        for channel in ('mm2s', 's2mm'):
            control = register_value(dma[channel + '_cr'])
            status = register_value(dma[channel + '_sr'])
            check(status & 0x1002 == 0x1002, channel + ' idle and completed interrupt recorded')
            check(not status & 0x779, channel + ' has no HALTED/SG/DMA error flags')
            check(bool(control & 1) and not control & 4, channel + ' run set and reset clear')
        check(register_value(dma['received_bytes']) == 2560, 'last DMA receive has exactly 640 uint32 words')
    except (KeyError, TypeError, ValueError) as exc:
        check(False, 'invalid or missing raw core/DMA diagnostics: ' + repr(exc))


def audit_query_sequence(rows, expected, check, label):
    """Bind every saved high-level sample to its ordered raw UART transaction."""
    check(len(rows) == len(expected), label + ' exact query count')
    for index, ((name, wanted), row) in enumerate(zip(expected, rows)):
        prefix = '%s item %d' % (label, index)
        check(row.get('query') == name, prefix + ' query order')
        result = row.get('result', {})
        check(all(result.get(key) == value for key, value in wanted.items()),
              prefix + ' sample bound to original decoded UART payload')


def audit_postflight_identity(session, after, postflight, check, result):
    """PYNQ's per-process download timestamp differs from global cache time.

    Keep strict same-process before/after equality elsewhere. A cross-process
    discrepancy requires the installed implementation, persistent bit hash and
    a second read-only hardware snapshot; it is never silently ignored.
    """
    check(postflight['final_bitfile'] == after['bitfile'] and
          postflight['fclk0_mhz'] == after['fclk0_mhz'],
          'postflight independently records the same overlay path/clock')
    if postflight['final_timestamp'] == after['timestamp']:
        check(True, 'postflight timestamp also matches the process timestamp')
        result['postflight_timestamp_basis'] = 'identical_process_and_global_timestamp'
        return
    implementation = session / 'pynq_implementation'
    files = ('pl.py', 'pl_server__device.py', 'pl_server__embedded_device.py',
             'pl_server__global_state.py')
    sources = {name: (implementation / name).read_text(encoding='utf-8') for name in files}

    def method(file, owner, name):
        tree = ast.parse(sources[file])
        owners = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == owner]
        nodes = owners[0].body if owner else tree.body
        node = next(item for item in nodes if isinstance(item, ast.FunctionDef) and item.name == name)
        return ast.unparse(node)

    getter = method('pl.py', 'PLMeta', 'timestamp')
    cache = method('pl_server__embedded_device.py', 'EmbeddedDevice', 'gen_cache')
    download = method('pl_server__embedded_device.py', 'EmbeddedDevice', 'download')
    post_download = method('pl_server__device.py', 'Device', 'post_download')
    reset = method('pl_server__device.py', 'Device', 'reset')
    hash_function = method('pl_server__global_state.py', None, 'bitstream_hash')
    check("hasattr(Device.active_device, 'timestamp')" in getter and
          getter.index('return Device.active_device.timestamp') < getter.index('return gs.timestamp'),
          'installed PL.timestamp prefers process attribute then global-state timestamp')
    check('datetime.datetime.now()' in cache and 'timestamp=ts' in cache and
          'save_global_state(gs)' in cache,
          'installed gen_cache creates a separate persistent wall-clock timestamp')
    check(download.index('self.gen_cache(bitstream, parser)') <
          download.index('with open(self.BS_FPGA_MAN,') <
          download.index('super().post_download(bitstream, parser, self.name)'),
          'installed download writes global cache before physical download and process timestamp')
    check('datetime.datetime.now()' in post_download and
          'bitstream.timestamp =' in post_download and
          'self.reset(parser, bitstream.timestamp, bitstream.bitfile_name)' in post_download and
          'self.timestamp = timestamp' in reset,
          'installed post_download assigns the later process-local timestamp')
    check('hashlib.sha1()' in hash_function and 'return h.hexdigest()' in hash_function,
          'installed GlobalState bitstream hash algorithm is SHA1')
    state_path = implementation / 'global_pl_state_.json'
    state = json.loads(state_path.read_text(encoding='utf-8'))
    bit = session / 'tested_sources/14_integrated_release_20261020/laser_motor.bit'
    check(state['bitfile_name'] == after['bitfile'] and
          state['timestamp'] == postflight['final_timestamp'] and
          state['bitfile_hash'] == hashlib.sha1(bit.read_bytes()).hexdigest(),
          'global cache timestamp/path/SHA1 match the archived tested bitstream')

    def parsed(value):
        # The formatter emits an unpadded integer microsecond, unlike %f.
        parts = re.fullmatch(r'(\d+)/(\d+)/(\d+) (\d+):(\d+):(\d+) \+(\d+)', value)
        if parts is None:
            raise ValueError('Unexpected PYNQ timestamp format')
        return datetime(*(int(part) for part in parts.groups()))

    delta = (parsed(after['timestamp']) - parsed(state['timestamp'])).total_seconds()
    check(delta >= 0, 'persistent cache timestamp precedes process post-download timestamp')
    snapshot_path = session / 'postflight_mmio_readonly.json'
    snapshot = json.loads(snapshot_path.read_text(encoding='utf-8'))
    check(snapshot['scope'] == 'read_only_no_overlay_download_no_register_writes' and
          snapshot['bitfile'] == after['bitfile'] and
          snapshot['timestamp'] == state['timestamp'] and
          snapshot['fclk0_mhz'] == after['fclk0_mhz'] and
          snapshot['core_id'] == 0x4C535431 and snapshot['core_version'] == 0x10000 and
          snapshot['fpga_manager_state'] == 'operating',
          'independent read-only postflight has same bit/clock and real core identity')
    # The second process cannot inspect the old Python owner's buffer list.
    # That list was already checked in completion.json; check raw MMIO here.
    audit_diagnostics(snapshot, check, expected_completed=68, check_quarantine=False)
    result['postflight_timestamp_basis'] = {
        'status': 'explained_by_installed_PYNQ_implementation_and_raw_MMIO',
        'process_post_download_timestamp': after['timestamp'],
        'persistent_pre_download_cache_timestamp': state['timestamp'],
        'process_minus_cache_seconds': delta,
        'scope': 'same-process equality and 1..68 hardware continuity still required',
        'installed_source_sha256': {
            name: hashlib.sha256((implementation / name).read_bytes()).hexdigest() for name in files},
        'global_state_sha256': hashlib.sha256(state_path.read_bytes()).hexdigest(),
        'readonly_MMIO_snapshot_sha256': hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
    }


def decoded_query(name, raw):
    """Independent Emm decoding; intentionally does not import motor drivers."""
    function, size = QUERY[name]
    assert len(raw) == size and raw[:2] == bytes([1, function]) and raw[-1] == 0x6B
    if name == 'configuration':
        assert raw[2:4] == bytes([33, 21])
        return {'firmware_family_layout': 'Emm', 'parameter_count': 21,
                'raw_configuration_hex': raw.hex(' ')}
    if name == 'position':
        assert raw[2] in (0, 1)
        value = int.from_bytes(raw[3:7], 'big') * (-1 if raw[2] else 1)
        return {'signed_position_raw': value, 'motor_position_deg': value * 360 / 65536}
    if name == 'voltage':
        return {'bus_voltage_mV': int.from_bytes(raw[2:4], 'big')}
    if name == 'encoder':
        value = int.from_bytes(raw[2:4], 'big')
        return {'encoder_count': value, 'motor_single_turn_angle_deg': value * 360 / 65536}
    if name == 'version':
        return {'firmware_version_code': int.from_bytes(raw[2:4], 'big'),
                'hardware_series_code': raw[4] >> 4,
                'hardware_type_code': raw[4] & 15,
                'hardware_revision_code': raw[5]}
    flags = raw[2]
    return dict(flags_hex='%02x' % flags, **{
        key: bool(flags & mask) for key, mask in (
            ('enabled', 1), ('position_reached', 2), ('stall_detected', 4),
            ('stall_protection_triggered', 8), ('left_limit_high', 16),
            ('right_limit_high', 32), ('power_off_marker', 128))})


def review(path):
    record = json.loads(path.read_text(encoding='utf-8'))
    result = {'scope': 'independent_downloaded_record_review_no_hardware',
              'record_file': str(path), 'record_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
              'check_count': 0, 'failures': [], 'query_counts': {}, 'points': []}

    def check(condition, label):
        result['check_count'] += 1
        if not condition:
            result['failures'].append(label)

    check(record.get('status') == 'integrated_static_and_small_scan_passed', 'top-level test passed')
    check(record.get('client_date') == '2026-10-20', 'client date')
    check(record.get('geometry_tested') is False, 'geometry acceptance is not claimed')
    check(record.get('same_overlay_throughout') is True, 'same-overlay flag')
    before, after = record.get('overlay_before', {}), record.get('overlay_after', {})
    check(bool(before) and before == after, 'overlay bit path/timestamp/clock unchanged')
    check(str(before.get('bitfile', '')).endswith('/14_integrated_release_20261020/laser_motor.bit'),
          'new combined release loaded')
    check(before.get('fclk0_mhz') == 50.0, '50 MHz clock')
    check(record.get('driver_healthy') is True and record.get('quarantined_buffers') == 0,
          'driver healthy with no retained DMA buffers')
    check(not record.get('recovery_hold'), 'no DMA recovery hold')
    static = record.get('static_cases', [])
    check(len(static) == 56 and all(case.get('passed') is True for case in static),
          'all 56 actual static comparisons passed')
    check([case.get('ordinal') for case in static] == list(range(1, 57)),
          'static ordinals are exactly 1..56')
    check([case.get('completed_frames') for case in static] == list(range(1, 57)),
          'static hardware frame counters are exactly 1..56')

    all_queries = []
    for key in ('before_overlay_readonly', 'integrated_uart_readonly'):
        rows = record.get(key, {}).get('transactions', [])
        check(bool(rows), key + ' has original UART transactions')
        all_queries.extend((key, row) for row in rows)
    scan = record.get('scan', {})
    check(scan.get('status') == 'completed' and scan.get('real_hardware_pipeline_verified') is True,
          'real hardware scan completed')
    check(scan.get('simulation') is False, 'not simulation')
    check(scan.get('requested_angles_deg') == [0, 5, 0], 'exact requested 0/5/0 angles')
    check(scan.get('motor_ready', {}).get('backend') == 'hardware_pynq_uartlite',
          'direct AXI UART backend')
    check(scan.get('motor_ready', {}).get('overlay_bitfile') == before.get('bitfile'),
          'motor uses the same combined overlay')
    all_queries.extend(('scan', row) for row in scan.get('read_transactions', []))
    counts = Counter()
    for index, (phase, row) in enumerate(all_queries):
        label = '%s query %d' % (phase, index)
        try:
            name = row['query']
            function = QUERY[name][0]
            expected = bytes([1, function]) + (b'\x6c' if name == 'configuration' else b'') + b'\x6b'
            tx, rx = bytes.fromhex(row['tx_hex']), bytes.fromhex(row['rx_hex'])
            check(tx == expected, label + ' exact request')
            decoded = decoded_query(name, rx)
            check(all(row['result'].get(key) == value for key, value in decoded.items()),
                  label + ' independently decoded payload')
            check(row.get('sent_bytes') == len(tx), label + ' full transmit')
            check(row.get('return_code', row.get('transport_return_code')) == len(rx),
                  label + ' receive length')
            check(not row.get('error') and not row.get('stale_rx_hex') and
                  not row.get('uart_status_observed_or', row.get('uart_status', 0)) & 0xE0,
                  label + ' clean UART transport')
            counts[name] += 1
        except Exception as exc:
            check(False, label + ' invalid: ' + repr(exc))
    result['query_counts'] = dict(counts)
    result['read_only_query_count'] = len(all_queries)
    try:
        before_read = record['before_overlay_readonly']
        samples = before_read['samples']
        check(len(samples) == 3, 'three motor samples before overlay replacement')
        expected = []
        for sample in samples:
            expected.extend([('status', sample['status']), ('position', sample['position'])])
            status = sample['status']
            check(status['position_reached'] and not status['stall_detected'] and
                  not status['stall_protection_triggered'], 'pre-overlay motor reached and no fault')
        positions = [sample['position']['motor_position_deg'] for sample in samples]
        check(all(math.isfinite(value) for value in positions) and max(positions) - min(positions) <= .1,
              'pre-overlay feedback remained within 0.1 degree')
        audit_query_sequence(before_read['transactions'], expected, check, 'pre-overlay samples')
        received = record['integrated_uart_readonly']['received']
        names = ('version', 'configuration', 'status', 'voltage', 'encoder', 'position')
        audit_query_sequence(record['integrated_uart_readonly']['transactions'],
                             [(name, received[name]) for name in names], check,
                             'combined-overlay initial read queries')
    except Exception as exc:
        check(False, 'pre-overlay or initial UART samples invalid: ' + repr(exc))

    motion = scan.get('motion_log', [])
    operations = [row.get('operation') for row in motion]
    check(operations == [0, 2, 2, 0, 0], 'exact commands: stop, forward, return, stop, close-stop')
    commands = []
    for index, row in enumerate(motion):
        label = 'motion command %d' % index
        try:
            tx, rx = bytes.fromhex(row['tx_hex']), bytes.fromhex(row['rx_hex'])
            check(tx[0] == 1 and tx[-1] == 0x6B, label + ' unicast/fixed checksum')
            check(rx == bytes([1, tx[1], 2, 0x6B]), label + ' accepted response')
            check(row.get('sent_bytes') == len(tx) and row.get('return_code') == 4,
                  label + ' exact transmitted/received lengths')
            check(not row.get('error') and not row.get('stale_rx_hex') and
                  not row.get('uart_status_observed_or', row.get('uart_status', 0)) & 0xE0,
                  label + ' no transport errors')
            if row['operation'] == 0:
                check(tx == bytes.fromhex('01 fe 98 00 6b'), label + ' exact stop')
            elif row['operation'] == 2:
                check(len(tx) == 13 and tx[1] == 0xFD and tx[2] in (0, 1),
                      label + ' finite move format')
                rpm, pulses = int.from_bytes(tx[3:5], 'big'), int.from_bytes(tx[6:10], 'big')
                check(rpm == 1 and pulses == 44 and tx[5] == 10 and tx[10:12] == bytes([2, 0]),
                      label + ' 1 RPM, 44 pulses, current-relative, no sync')
                commands.append({'direction': tx[2], 'rpm': rpm, 'pulses': pulses, 'tx_hex': tx.hex(' ')})
            else:
                check(False, label + ' unexpected enable/home/config/velocity operation')
        except Exception as exc:
            check(False, label + ' invalid: ' + repr(exc))
    check(len(commands) == 2 and [row['direction'] for row in commands] == [0, 1],
          'exactly two opposite FD movements')
    result.update(exact_command_operations=operations, position_commands=commands,
                  stop_commands=operations.count(0), enable_commands=operations.count(1))

    try:
        geometry = scan['motor_ready']['geometry']
        check(geometry == {'step_angle_deg': 1.8, 'microsteps': 16,
                           'motor_turns_per_table_turn': 1.0}, 'known motor geometry')
        origin = scan['motor_origin_deg']
        result['origin_motor_deg'] = origin
        points = scan['points']
        check(len(points) == 3, 'three completed acquisition poses')
        check(points[0]['move'].get('initial_settle_reused') is True and
              points[0]['move'].get('motion_command_required') is False,
              'initial origin uses the settled state without an FD command')
        ready = scan['motor_ready']
        read_sequence = [('configuration', ready['configuration']), ('status', {}), ('position', {})]
        for sample in ready['initial_settle']['samples']:
            read_sequence.extend([('status', sample['status']), ('position', sample['position'])])
        read_sequence.append(('voltage', ready['voltage']))
        for index, point in enumerate(points):
            label = 'point %d' % index
            check(point['index'] == index and point['requested_table_relative_deg'] == [0, 5, 0][index],
                  label + ' index and requested angle')
            check(point['target_motor_deg'] == origin + [0, 5, 0][index],
                  label + ' requested motor target uses recorded origin')
            check(point['status'] == 'completed', label + ' completed')
            settled = point['move']['settled']
            target, measured = settled['target_deg'], settled['motor_position_deg']
            check(point['move']['motor_position_deg'] == measured, label + ' move and settle feedback agree')
            check(math.isfinite(measured) and abs(measured - target) <= .3, label + ' target tolerance')
            samples = settled['samples']
            check(len(samples) >= 3, label + ' at least three settle samples')
            sample_times = [sample['sample_monotonic'] for sample in samples]
            check(all(math.isfinite(value) for value in sample_times) and
                  all(a < b for a, b in zip(sample_times, sample_times[1:])),
                  label + ' strictly increasing finite settle timestamps')
            check(measured == samples[-1]['position']['motor_position_deg'],
                  label + ' final feedback is the last recorded sample')
            if index == 0:
                check(settled == ready['initial_settle'] and measured == origin,
                      label + ' initial motor preparation evidence reused exactly')
            else:
                plan = point['move']['plan']
                current = plan['requested_target_motor_deg'] - plan['requested_delta_deg']
                read_sequence.append(('position', {'motor_position_deg': current}))
                for sample in samples:
                    read_sequence.extend([('status', sample['status']), ('position', sample['position'])])
                check(plan['requested_target_motor_deg'] == point['target_motor_deg'] and
                      plan['quantized_target_motor_deg'] == target,
                      label + ' requested and quantized targets match movement plan')
                sign = 1 if index == 1 else -1
                check(plan['pulses'] == 44 and plan['rpm'] == 1 and plan['direction'] == index - 1 and
                      plan['quantized_delta_deg'] == sign * 44 * 1.8 / 16 and
                      abs(target - current - plan['quantized_delta_deg']) < 1e-12,
                      label + ' feedback-linked 44-pulse relative plan')
            good_start = None
            for sample in samples:
                status = sample['status']
                value = sample['position']['motor_position_deg']
                good = (status['enabled'] and status['position_reached'] and
                        not status['stall_detected'] and not status['stall_protection_triggered']
                        and abs(value - target) <= .3)
                if good and good_start is None:
                    good_start = sample['sample_monotonic']
                if not good:
                    good_start = None
            duration = samples[-1]['sample_monotonic'] - good_start if good_start is not None else 0
            values = [sample['position']['motor_position_deg'] for sample in samples[-3:]]
            span = max(values) - min(values)
            check(duration >= 2 and span <= .1, label + ' independently checked two-second dwell/0.1 degree span')
            capture_before, capture_after = point['motor_position_before_capture_deg'], point['motor_position_after_capture_deg']
            check(capture_before == measured, label + ' capture starts from settled motor feedback')
            read_sequence.append(('position', {'motor_position_deg': capture_after}))
            check(abs(capture_after - capture_before) <= .1, label + ' stable through camera capture')
            check(point['table_relative_deg_from_motor_feedback'] == capture_before - origin,
                  label + ' feedback origin arithmetic')
            capture = point['capture']
            check(capture['source'] == 'hardware_camera' and capture['exposure'] == 5 and
                  capture['stripe_coordinate_source'] == 'supplied_PL_driver' and
                  capture['stripe_quality']['image_quality_gate_passed'] is True,
                  label + ' exposure 5 + actual PL + quality gate')
            result['points'].append({'requested_deg': point['requested_table_relative_deg'],
                                     'feedback_relative_deg': capture_before - origin,
                                     'capture_drift_deg': capture_after - capture_before,
                                     'independent_dwell_s': duration, 'last_three_span_deg': span})
        check(scan['preflight_capture']['stripe_quality']['image_quality_gate_passed'] is True,
              'stationary camera quality gate passed before motor preparation')
        audit_query_sequence(scan['read_transactions'], read_sequence, check, 'complete scan feedback sequence')
        cleanup = scan['cleanup']
        check(cleanup.get('motor_stop_confirmed') is True and cleanup.get('motor_client_closed') is True,
              'final stop acceptance and motor close confirmed')
        camera = cleanup['camera']
        check(camera.get('camera_release_confirmed') is True and
              camera.get('exposure_restoration_verified') is True and
              camera.get('no_active_capture_process') is True and camera.get('capture_count') == 4,
              'four camera batches all restored/released')
    except Exception as exc:
        check(False, 'scan structure or settle evidence invalid: ' + repr(exc))

    try:
        scan_root = path.parent / 'scan'
        events = [json.loads(line) for line in (scan_root / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
        expected_events = ['starting', 'camera_ready', 'preflight_capture_passed', 'motor_ready']
        expected_events += ['move_start', 'settled', 'point_saved'] * 3 + ['closed']
        check([event['event'] for event in events] == expected_events, 'full acquisition stage order')
        times = [event['elapsed_s'] for event in events]
        check(all(math.isfinite(value) and value >= 0 for value in times) and
              all(a <= b for a, b in zip(times, times[1:])), 'monotonic acquisition stage times')
        disk_scan = json.loads((scan_root / 'session.json').read_text(encoding='utf-8'))
        check(disk_scan == scan, 'scan/session.json matches top-level embedded scan')
        frame_counters = [case['completed_frames'] for case in static]
        for phase in ('preflight', 'point_00', 'point_01', 'point_02'):
            folder = scan_root / phase / 'images'
            transactions = json.loads((folder / 'pl_transactions.json').read_text(encoding='utf-8'))
            check([row['frame'] for row in transactions] == [0, 1, 2], phase + ' three ordered actual PL frames')
            for row in transactions:
                check(row['source'] == 'supplied_PL_driver' and row['PS_reference_computed'] is True and
                      row['valid_equal'] is True and 0 <= row['max_pixel_difference'] <= .25 and
                      row['cycles'] > 0, phase + ' actual PL comparison and positive cycle count')
                output = folder / ('pl_%02d_actual.bin' % row['frame'])
                check(output.is_file() and output.stat().st_size == 2560, phase + ' complete raw PL output')
                frame_counters.append(row['completed_frames'])
        check(frame_counters == list(range(1, 69)), 'all 68 hardware counters continuous across static and every capture')
        result['recorded_pl_frame_count'] = len(frame_counters)
        audit_diagnostics(record.get('diagnostics', {}), check, expected_completed=68)
        postflight = json.loads((path.parent.parent / 'postflight.json').read_text(encoding='utf-8'))
        check(postflight['process_state'] in ('exited', 'Z') and
              postflight['camera_fuser_returncode'] == 1 and not postflight['camera_owners'],
              'postflight confirms test exited and camera has no owner')
        audit_postflight_identity(path.parent.parent, after, postflight, check, result)
    except Exception as exc:
        check(False, 'phase files, continuous PL counters or postflight invalid: ' + repr(exc))

    try:
        source_root = path.parent.parent / 'tested_sources'
        source_manifest = json.loads((source_root / 'SHA256.json').read_text(encoding='utf-8'))
        source_entries = {entry['path']: entry for entry in source_manifest['files']}
        source_hashes = record.get('source_hashes', {})
        runtime_names = sorted(item.name for item in (source_root / '04_runtime').glob('*.py'))
        check(bool(runtime_names) and sorted(source_hashes) == runtime_names,
              'all tested runtime Python files have recorded hashes')
        for name in runtime_names:
            relative = '04_runtime/' + name
            tested = source_root / relative
            digest = hashlib.sha256(tested.read_bytes()).hexdigest()
            check(source_hashes.get(name) == digest == source_entries[relative]['sha256'],
                  relative + ' execution hash matches archived source and upload manifest')
            check(hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == digest,
                  relative + ' current engineering source matches actual tested source')
        runner = '09_tools/run_integrated_board.py'
        check(hashlib.sha256((source_root / runner).read_bytes()).hexdigest() ==
              source_entries[runner]['sha256'] ==
              hashlib.sha256((ROOT / runner).read_bytes()).hexdigest(),
              'actual board runner matches upload manifest and current engineering source')
        release = source_root / '14_integrated_release_20261020'
        release_manifest = json.loads((release / 'SHA256.json').read_text(encoding='utf-8'))
        release_entries = {entry['path']: entry for entry in release_manifest['files']}
        for name in ('laser_motor.bit', 'laser_motor.hwh'):
            file = release / name
            digest = hashlib.sha256(file.read_bytes()).hexdigest()
            relative = '14_integrated_release_20261020/' + name
            check(file.stat().st_size == release_entries[name]['bytes'] and
                  digest == release_entries[name]['sha256'] == source_entries[relative]['sha256'],
                  relative + ' tested artifact matches both release and upload manifests')
        result['tested_runtime_source_count'] = len(runtime_names)
    except Exception as exc:
        check(False, 'archived test source or release identity invalid: ' + repr(exc))
    result['all_checks_passed'] = not result['failures']
    result['status'] = ('independent_integrated_motor_review_passed' if result['all_checks_passed']
                        else 'independent_integrated_motor_review_failed')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('completion', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = review(args.completion.resolve())
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + '\n', encoding='utf-8')
    print(text)
    return 0 if result['all_checks_passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
