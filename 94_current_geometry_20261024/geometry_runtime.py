"""Unchanged61 XYZ functions with an explicit current-evidence provider.

Only this separate module instance receives the new evidence hook. Original
source, hardware compatibility configuration and other imported instances stay
unchanged. The95 capture manifest pins this adapter and every calibration file.
"""
from pathlib import Path
import importlib.util,hashlib,json
G=Path(__file__).resolve().parent;P=G.parent
def module(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
manifest=json.loads((G/'manifest.json').read_text());source=P/'61_current_axis_laser_release_20261024/fast_reconstruct_on_zynq.py'
if hashlib.sha256(source.read_bytes()).hexdigest()!=manifest['original61_reconstruction_sha256']:raise ValueError('Original reconstruction source changed')
evidence=module('geometry94_bound_evidence',G/'geometry_evidence.py')
native=module('geometry94_native_math',source)
native.previous_calibration_evidence=evidence.validate_geometry_evidence
def __getattr__(name):return getattr(native,name)
