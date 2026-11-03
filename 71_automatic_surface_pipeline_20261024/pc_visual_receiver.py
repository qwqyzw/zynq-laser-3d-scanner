"""Receive/hash/display Zynq-produced faces; PC performs no reconstruction."""
from pathlib import Path
import sys,json,hashlib,subprocess,os,webbrowser
HERE=Path(__file__).resolve().parent;P=HERE.parent
sys.path.insert(0,str(P/'70_pc_cancel_compatibility_20261024'))
import pc_cancel_receiver as cancellation
base=cancellation.receiver
old_download=base.download_export;old_present=base.present

def download(sftp,remote_root,record,output,**kwargs):
    directory,receipt=old_download(sftp,remote_root,record,output,**kwargs)
    if 'surface_relative' not in record:return directory,receipt
    expected='runs/'+record['run_id']+'/model/visual_model'
    if record['surface_relative']!=expected:raise ValueError('Surface path belongs to another run')
    remote=remote_root+'/'+expected;raw=base.read_remote(sftp,remote+'/artifact_manifest.json',100000)
    if hashlib.sha256(raw).hexdigest()!=record['surface_manifest_sha256']:raise ValueError('Surface manifest mismatch')
    manifest=json.loads(raw)
    if manifest.get('coordinate_producer')!='zynq' or manifest.get('derived_estimate') is not True or manifest.get('historical_algorithm_preview') is not False:
        raise ValueError('Live Zynq-derived surface required')
    dest=directory.parent/'visual_model';dest.mkdir(exist_ok=True)
    if not 3<=len(manifest['files'])<=16:raise ValueError('Unexpected surface file count')
    for name,row in manifest['files'].items():
        if Path(name).name!=name or not 0<row['bytes']<20000000:raise ValueError('Invalid surface entry')
        data=base.read_remote(sftp,remote+'/'+name,row['bytes']+1)
        if len(data)!=row['bytes'] or hashlib.sha256(data).hexdigest()!=row['sha256']:raise ValueError('Surface SHA mismatch')
        (dest/name).write_bytes(data)
    if base.read_remote(sftp,remote+'/artifact_manifest.json',100000)!=raw:raise ValueError('Surface changed during receive')
    meta=json.loads((dest/'model_metadata.json').read_text(encoding='utf-8'))
    if meta['coordinate_producer']!='zynq' or meta['historical_algorithm_preview'] or meta['source_run_id']!=record['run_id']:
        raise ValueError('Surface source identity differs')
    if meta['source_sha256']!=hashlib.sha256((directory/'points.csv').read_bytes()).hexdigest():raise ValueError('Surface uses another measured cloud')
    (dest/'artifact_manifest.json').write_bytes(raw)
    return directory,{**receipt,'surface_received':True,'surface_manifest_sha256':record['surface_manifest_sha256']}

def present(directory,mode):
    directory=Path(directory);surface=directory.parent/'visual_model'
    if not (surface/'artifact_manifest.json').is_file():return old_present(directory,mode)
    manifest=json.loads((surface/'artifact_manifest.json').read_text(encoding='utf-8'))
    for name,row in manifest['files'].items():
        if hashlib.sha256((surface/name).read_bytes()).hexdigest()!=row['sha256']:raise ValueError('Local surface changed')
    receipt={**base.verify_export(directory),'display_mode':mode,'gui_launched':False,
       'coordinate_producer':'zynq','pc_coordinate_computation':False,'estimated_surface':True,'surface_file':str(surface/'ESTIMATED_CONVEX_SURFACE.ply')}
    if mode=='cloudcompare':
        exe=Path(os.environ['LOCALAPPDATA'])/'Programs/CloudCompare/CloudCompare_v2.13.2.preview_bin_x64/CloudCompare.exe'
        if not exe.is_file():raise ValueError('Installed CloudCompare not found')
        proc=subprocess.Popen([str(exe),str(surface/'ESTIMATED_CONVEX_SURFACE.ply')])
        receipt.update(gui_launched=True,cloudcompare_pid=proc.pid,cloudcompare_executable=str(exe))
    elif mode=='html':receipt['gui_launched']=webbrowser.open((surface/'model_viewer.html').as_uri(),new=2)
    elif mode!='none':raise ValueError('Unknown display mode')
    return receipt

if __name__=='__main__':
    base.download_export=download;base.present=present
    raise SystemExit(cancellation.main())
