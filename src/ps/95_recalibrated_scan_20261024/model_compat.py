"""Run frozen89 model computation with explicit86/91 cleanup compatibility."""
from pathlib import Path
import sys,types,runpy
HERE=Path(__file__).resolve().parent;P=HERE.parent
sys.path.insert(0,str(HERE))
from release91 import verify_release
key='--capture-manifest-sha256';i=sys.argv.index(key);pin=sys.argv[i+1];del sys.argv[i:i+2]
verify_release(pin)
import compat_cleanup
proxy=types.ModuleType('cleanup86');proxy.inspect_cleanup=compat_cleanup.inspect_cleanup
sys.modules['cleanup86']=proxy
runpy.run_path(str(P/'89_multi_shape_display_20261024/model_worker.py'),run_name='__main__')
