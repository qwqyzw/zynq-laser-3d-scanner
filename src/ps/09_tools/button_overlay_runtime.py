"""Read-only identity checks for the separately versioned button overlay.

This module never programs the FPGA, opens GPIO, or sends motor commands.
The accepted 14 release remains immutable and is the software parent of 17.
"""
from pathlib import Path
import hashlib
import json
import xml.etree.ElementTree as ET

VERSION = '20261020.pl_uart_button.v1'
GPIO_ADDRESS = 0x41200000
REQUIRED_SOURCES = (
    '09_tools/button_overlay_runtime.py', '09_tools/object_board_scan.py',
    '09_tools/reconstruct_object_on_zynq.py', '09_tools/board_button_scan_service.py',
    '09_tools/load_button_overlay.py',
)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def child(root, name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name:
        raise ValueError('Invalid button release relative path')
    base = Path(root).resolve()
    item = base / name
    if Path(name).is_absolute() or '..' in Path(name).parts or not item.resolve().is_relative_to(base):
        raise ValueError('Button release path escapes selected root')
    if item.is_symlink() or not item.is_file():
        raise ValueError('Button release requires an ordinary file: ' + name)
    return item


def inspect_button_release(project, release):
    project, release = Path(project).resolve(), Path(release).resolve()
    if not release.is_relative_to(project) or release == project:
        raise ValueError('Button release must be a versioned directory inside selected project')
    manifest_path = child(release, 'SHA256.json')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    if manifest.get('version') != VERSION:
        raise ValueError('Unexpected button release version')
    parent = child(project, '14_integrated_release_20261020/SHA256.json')
    if manifest.get('parent_release_manifest_sha256') != digest(parent):
        raise ValueError('Button release does not bind the original integrated parent')
    records = manifest.get('files')
    if not isinstance(records, list) or not records:
        raise ValueError('Button release file manifest is missing')
    entries = {}
    for row in records:
        name = row['path']
        if name in entries:
            raise ValueError('Duplicate button release artifact: ' + name)
        path = child(release, name)
        if type(row.get('bytes')) is not int or row['bytes'] != path.stat().st_size:
            raise ValueError('Button artifact byte length differs: ' + name)
        actual = digest(path)
        if actual != row.get('sha256'):
            raise ValueError('Button artifact SHA256 differs: ' + name)
        entries[name] = {'absolute_path': str(path), 'bytes': path.stat().st_size, 'sha256': actual}
    for name in ('laser_motor_button.bit', 'laser_motor_button.hwh'):
        if name not in entries:
            raise ValueError('Missing button overlay artifact: ' + name)
    sources = manifest.get('source_sha256', {})
    if not isinstance(sources, dict) or not set(REQUIRED_SOURCES).issubset(sources):
        raise ValueError('Button release lacks explicit runtime source bindings')
    for name, expected in sources.items():
        if digest(child(project, name)) != expected:
            raise ValueError('Button release source changed: ' + name)
    hwh = ET.parse(entries['laser_motor_button.hwh']['absolute_path']).getroot()
    modules = {m.get('INSTANCE'): m for m in hwh.findall('.//MODULE')}

    def parameters(name):
        if name not in modules:
            raise ValueError('Button HWH lacks required IP: ' + name)
        return {p.get('NAME'): p.get('VALUE') for p in modules[name].findall('./PARAMETERS/PARAMETER')}

    for name, address in (('axi_dma_0', 0x40400000), ('axi_uartlite_0', 0x42C00000),
                          ('laser_stripe_0', 0x43C00000), ('axi_gpio_scan', GPIO_ADDRESS)):
        values = parameters(name)
        if int(values.get('C_BASEADDR', '-1'), 0) != address:
            raise ValueError('Button HWH changes required register address: ' + name)
    values = parameters('axi_gpio_scan')
    for name, expected in (('C_GPIO_WIDTH', 4), ('C_GPIO2_WIDTH', 4), ('C_IS_DUAL', 1),
                           ('C_ALL_INPUTS', 1), ('C_ALL_OUTPUTS_2', 1)):
        if int(values.get(name, '-1'), 0) != expected:
            raise ValueError('Button GPIO configuration differs: ' + name)
    if int(parameters('axi_uartlite_0').get('C_BAUDRATE', '-1')) != 9600:
        raise ValueError('Button HWH changes the turntable UART baud rate')
    return {'version': VERSION, 'relative_directory': release.relative_to(project).as_posix(),
            'manifest_path': str(manifest_path), 'manifest_sha256': digest(manifest_path),
            'source_sha256': sources, 'laser_motor_button.bit': entries['laser_motor_button.bit'],
            'laser_motor_button.hwh': entries['laser_motor_button.hwh'],
            'gpio': {'ip': 'axi_gpio_scan', 'address': GPIO_ADDRESS, 'input_width': 4, 'output_width': 4},
            'hardware_accessed': False, 'inspection_only': True}


def selected_bit(artifacts):
    if 'button_release' in artifacts:
        return artifacts['button_release']['laser_motor_button.bit']
    return artifacts['integrated_release']['laser_motor.bit']
