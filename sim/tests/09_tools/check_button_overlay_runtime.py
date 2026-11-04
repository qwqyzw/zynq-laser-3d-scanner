"""Synthetic file-identity tests only; no board, real XYZ, GPIO, or motor access."""
from pathlib import Path
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET

import button_overlay_runtime as runtime


class ReleaseIdentity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='button_release_test_')
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.release = self.project / '17_button_test'
        self.release.mkdir()
        parent = self.project / '14_integrated_release_20261020'
        parent.mkdir()
        (parent / 'SHA256.json').write_text('{"test_only":true}', encoding='utf-8')
        for name in runtime.REQUIRED_SOURCES:
            file = self.project / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text('# synthetic source identity only\n', encoding='utf-8')
        root = ET.Element('SYSTEMINFO')
        modules = ET.SubElement(root, 'MODULES')
        for ip, address in (('axi_dma_0', 0x40400000), ('axi_uartlite_0', 0x42C00000),
                            ('laser_stripe_0', 0x43C00000), ('axi_gpio_scan', 0x41200000)):
            module = ET.SubElement(modules, 'MODULE', INSTANCE=ip)
            parameters = ET.SubElement(module, 'PARAMETERS')
            values = {'C_BASEADDR': hex(address)}
            if ip == 'axi_uartlite_0':
                values['C_BAUDRATE'] = '9600'
            if ip == 'axi_gpio_scan':
                values.update(C_GPIO_WIDTH='4', C_GPIO2_WIDTH='4', C_IS_DUAL='1',
                              C_ALL_INPUTS='1', C_ALL_OUTPUTS_2='1')
            for name, value in values.items():
                ET.SubElement(parameters, 'PARAMETER', NAME=name, VALUE=value)
        ET.ElementTree(root).write(self.release / 'laser_motor_button.hwh')
        (self.release / 'laser_motor_button.bit').write_bytes(b'SYNTHETIC IDENTITY FIXTURE ONLY')
        self.manifest = {'version': runtime.VERSION, 'test_only': True,
                         'parent_release_manifest_sha256': runtime.digest(parent / 'SHA256.json'),
                         'source_sha256': {name: runtime.digest(self.project / name)
                                           for name in runtime.REQUIRED_SOURCES}}
        self.bind()

    def bind(self):
        self.manifest['files'] = [
            {'path': name, 'bytes': (self.release / name).stat().st_size,
             'sha256': runtime.digest(self.release / name)}
            for name in ('laser_motor_button.bit', 'laser_motor_button.hwh')]
        self.save()

    def save(self):
        (self.release / 'SHA256.json').write_text(json.dumps(self.manifest), encoding='utf-8')

    def inspect(self):
        return runtime.inspect_button_release(self.project, self.release)

    def test_new_selection_is_explicit_and_parent_unchanged(self):
        before = runtime.digest(self.project / '14_integrated_release_20261020/SHA256.json')
        selected = self.inspect()
        self.assertEqual(runtime.selected_bit({'button_release': selected})['absolute_path'],
                         str(self.release / 'laser_motor_button.bit'))
        self.assertEqual(runtime.selected_bit({'integrated_release': {'laser_motor.bit': 'old'}}), 'old')
        self.assertEqual(selected['gpio']['address'], 0x41200000)
        self.assertFalse(selected['hardware_accessed'])
        self.assertEqual(before, runtime.digest(self.project / '14_integrated_release_20261020/SHA256.json'))

    def test_changed_bit_rejected(self):
        (self.release / 'laser_motor_button.bit').write_bytes(b'changed')
        with self.assertRaises(ValueError): self.inspect()

    def test_source_change_rejected(self):
        (self.project / runtime.REQUIRED_SOURCES[0]).write_text('changed', encoding='utf-8')
        with self.assertRaises(ValueError): self.inspect()

    def test_parent_change_rejected(self):
        (self.project / '14_integrated_release_20261020/SHA256.json').write_text('changed', encoding='utf-8')
        with self.assertRaises(ValueError): self.inspect()

    def test_wrong_gpio_address_even_with_consistent_hash_rejected(self):
        path = self.release / 'laser_motor_button.hwh'
        text = path.read_text().replace('0x41200000', '0x41210000')
        path.write_text(text, encoding='utf-8')
        self.bind()
        with self.assertRaises(ValueError): self.inspect()

    def test_path_escape_and_duplicate_rejected(self):
        self.manifest['files'][0]['path'] = '../outside.bit'
        self.save()
        with self.assertRaises(ValueError): self.inspect()
        self.bind()
        self.manifest['files'].append(self.manifest['files'][0])
        self.save()
        with self.assertRaises(ValueError): self.inspect()


if __name__ == '__main__':
    unittest.main()
