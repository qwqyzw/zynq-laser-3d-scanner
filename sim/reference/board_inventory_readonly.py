"""Run on PYNQ only: read versions and loaded-IP metadata, no bitstream loading.

Does not open camera, query motor, read credentials, or send network requests.
"""
import json,platform
from pathlib import Path
import hashlib
def main():
    try:
        import pynq
        from pynq import PL
    except ImportError:raise SystemExit('This read-only inventory runs on the PYNQ board, not the teammate PC.')
    result={'pynq_version':getattr(pynq,'__version__','unknown'),'python':platform.python_version(),'machine':platform.machine(),'motion_or_capture_requested':False,'bitstream_loaded_by_this_script':False}
    try:
        result['currently_recorded_bitfile']=str(PL.bitfile_name)
        result['ip_names']=sorted(PL.ip_dict)
        result['hierarchy_names']=sorted(PL.hierarchy_dict)
        f=Path(result['currently_recorded_bitfile'])
        if f.is_file():result['bitfile_sha256']=hashlib.sha256(f.read_bytes()).hexdigest()
        hwh=f.with_suffix('.hwh')
        if hwh.is_file():result['hwh_sha256']=hashlib.sha256(hwh.read_bytes()).hexdigest()
    except Exception as exc:result['metadata_error']=str(exc)
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
