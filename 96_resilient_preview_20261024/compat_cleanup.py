"""Exact historical86/91 and current95 receipts, with no relaxed cleanup."""
from pathlib import Path
import importlib.util,sys,json,hashlib
HERE=Path(__file__).resolve().parent;P=HERE.parent
PIN86='06e6c6cc0167c16832edf85a0470499f7744062aeca473cf8e80f015a88d60b5'
PIN95='70fe462f2d3e6891f78c154970a3119ffd6034a4860c1a1f46ccc1c2cb76f1cf'
PIN91='69b294fac8b22ae92b67045d7ccaf16984aea8823f044e6d1825a20f4e3276b1'
def module(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
def historical(folder,release_name,cleanup_name):
 release=module('historical_'+folder+'_release',P/folder/(release_name+'.py'));saved=sys.modules.get(release_name);sys.modules[release_name]=release
 try:return module('historical_'+folder+'_cleanup',P/folder/(cleanup_name+'.py'))
 finally:
  if saved is None:sys.modules.pop(release_name,None)
  else:sys.modules[release_name]=saved
old86=historical('86_integer_reference_fix_20261024','release86','cleanup86')
old91=historical('91_scene_demo_release_20261024','release91','cleanup91')
old95=historical('95_recalibrated_scan_20261024','release91','cleanup91')
def inspect_cleanup(path,pin):
 data=json.loads((Path(path)/'trial.json').read_text());new=hashlib.sha256((HERE/'manifest.json').read_bytes()).hexdigest()
 if pin not in [PIN86,PIN91,PIN95,new]:raise ValueError('Unknown cleanup caller binding')
 actual=data.get('release_manifest_sha256')
 if actual==PIN86:return old86.inspect_cleanup(path,PIN86)
 if actual==PIN91:return old91.inspect_cleanup(path,PIN91)
 if actual==PIN95:return old95.inspect_cleanup(path,PIN95)
 if actual==new:
  sys.path.insert(0,str(HERE));from cleanup91 import inspect_cleanup as current
  return current(path,new)
 raise ValueError('Unrecognized capture release; no cleanup bypass')
