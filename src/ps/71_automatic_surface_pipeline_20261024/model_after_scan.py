"""Math-only child: derive visualization after frozen61 camera/DMA cleanup."""
from pathlib import Path
import argparse,json,sys,hashlib
P=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(P/'69_surface_visualization_20261024'))
from surface_model import run,save,sha

def process(root):
    root=Path(root).resolve()
    expected=Path('/home/xilinx/jupyter_notebooks/button_scan/runs').resolve()
    import re
    if root.parent!=expected or not re.fullmatch('[0-9a-f]{32}',root.name):raise ValueError('Run outside expected scanner root')
    completion=json.loads((root/'scan/completion.json').read_text());cleanup=completion.get('cleanup',{})
    if not (completion.get('status')=='completed' and cleanup.get('motor_stop_confirmed') and
            cleanup.get('camera',{}).get('camera_release_confirmed') and completion.get('driver_healthy') and
            completion.get('quarantined_buffers')==0):raise ValueError('Acquire/cleanup not complete')
    meta=json.loads((root/'model/object_metadata.json').read_text())
    if meta.get('data_origin')!='hardware_scan' or meta.get('coordinate_producer')!='zynq':raise ValueError('Real board source required')
    source=root/'model/object_points.csv';out=root/'model/visual_model'
    report=run(source,sha(source),out,root.name,archive=False)
    html=out/'model_viewer.html'
    html.write_text(html.read_text(encoding='utf-8').replace('历史数据算法验证','本轮扫描算法结果').replace('（历史算法预览）','（本轮估计表面）'),encoding='utf-8')
    # The prototype PNG contains an archival caption; the live viewer/mesh are
    # the deliverables here. Do not publish a mislabelled historical PNG.
    (out/'model_preview.png').unlink()
    save(out/'artifact_manifest.json',{'coordinate_producer':'zynq','derived_estimate':True,'historical_algorithm_preview':False,
        'files':{p.name:{'sha256':sha(p),'bytes':p.stat().st_size} for p in sorted(out.iterdir()) if p.is_file() and p.name!='artifact_manifest.json'}})
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--run',type=Path,required=True);args=parser.parse_args()
    try:result=process(args.run)
    except Exception as e:result={'status':'surface_unavailable','error':type(e).__name__+': '+str(e)}
    save(args.run/'model/surface_job.json',result);print(json.dumps(result,ensure_ascii=False))
    raise SystemExit(0 if result['status']=='derived_visual_model_exported' else 1)
