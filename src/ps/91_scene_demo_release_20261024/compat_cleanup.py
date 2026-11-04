"""Recognize exactly frozen86 or this pinned91 capture, preserving all checks."""
from pathlib import Path
import importlib.util,sys,json,types,hashlib
HERE=Path(__file__).resolve().parent;P=HERE.parent
PIN86='06e6c6cc0167c16832edf85a0470499f7744062aeca473cf8e80f015a88d60b5'
def module(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
old_release=module('original86_release',P/'86_integer_reference_fix_20261024/release86.py')
saved=sys.modules.get('release86');sys.modules['release86']=old_release
try:old_cleanup=module('original86_cleanup',P/'86_integer_reference_fix_20261024/cleanup86.py')
finally:
 if saved is None:sys.modules.pop('release86',None)
 else:sys.modules['release86']=saved
def inspect_cleanup(path,pin):
 data=json.loads((Path(path)/'trial.json').read_text());new_pin=hashlib.sha256((HERE/'manifest.json').read_bytes()).hexdigest()
 if pin not in [PIN86,new_pin]:raise ValueError('Unknown cleanup caller binding')
 if data.get('release_manifest_sha256')==PIN86:return old_cleanup.inspect_cleanup(path,PIN86)
 if data.get('release_manifest_sha256')==new_pin:
  sys.path.insert(0,str(HERE));from cleanup91 import inspect_cleanup as inspect91
  return inspect91(path,new_pin)
 raise ValueError('Unrecognized capture release; no cleanup bypass')
