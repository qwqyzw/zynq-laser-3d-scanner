"""Frozen101 cleanup binding, new file-only104 model worker."""
from pathlib import Path
import sys,types,runpy,json,hashlib
HERE=Path(__file__).resolve().parent
P=HERE.parent
CAP=P/'101_relocation_scan_20261102'
sys.path.insert(0,str(CAP))
from release91 import verify_release
verify_release('ea4e1aea382a1b976719c18c3afa1001459a12674c5b2d3dc01acf174f77f571')
import compat_cleanup
proxy=types.ModuleType('cleanup86')
proxy.inspect_cleanup=compat_cleanup.inspect_cleanup
sys.modules['cleanup86']=proxy
sys.path.insert(0,str(HERE))
runpy.run_path(str(HERE/'model_worker.py'),run_name='__main__')
