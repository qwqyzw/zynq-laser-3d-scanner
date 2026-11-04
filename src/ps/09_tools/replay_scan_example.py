"""Offline3-profile replay with matched historical models; never hardware."""
from pathlib import Path
import argparse,json,hashlib,sys
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'04_runtime'))
from scan_stripe_quality import common_stripe_pixels
from geometric_calibration import pixel_rays
from geometry_core import reconstruct
from turntable_coordinates import to_initial_table_frame

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,default=ROOT/'results/offline_replay');a=ap.parse_args()
    a.output.mkdir(parents=True,exist_ok=False)
    models=ROOT/'05_geometry/shape180_20261018';sample=ROOT/'08_scan_example'
    freeze=json.loads((models/'freeze.json').read_text())
    for n in ['camera_reused.json','laser_frozen.json','paper_reference_frozen.json','rotation_axis_current.json']:
        assert hashlib.sha256((models/n).read_bytes()).hexdigest()==freeze[n+'_sha256'],n
    camera=json.loads((models/'camera_reused.json').read_text());laser=json.loads((models/'laser_frozen.json').read_text())
    paper=json.loads((models/'paper_reference_frozen.json').read_text())['pose'];axis=json.loads((models/'rotation_axis_current.json').read_text())
    checks=[]
    for g in json.loads((sample/'example.json').read_text())['profiles']:
        pixels,quality=common_stripe_pixels([sample/n for n in g['images']],tuple(g['roi']))
        xyz,height,valid=reconstruct(pixel_rays(pixels,camera),laser,paper);assert valid.all()
        table=to_initial_table_frame(xyz,g['axis_angle_deg'],axis)
        expected=np.loadtxt(sample/g['expected_csv'],delimiter=',',skiprows=1)
        actual=np.column_stack([pixels,xyz,table,height]);wanted=expected[:,[3,4,5,6,7,8,9,10,11]]
        assert actual.shape==wanted.shape and np.allclose(actual,wanted,rtol=0,atol=1e-9),g['index']
        np.savetxt(a.output/f'profile_{g["index"]:02d}.csv',actual,delimiter=',',header='x_px,y_px,camera_X,camera_Y,camera_Z,table_X,table_Y,table_Z,height_mm',comments='')
        checks.append({'index':g['index'],'points':len(pixels),'max_numeric_difference':float(np.max(abs(actual-wanted))),'passed':True})
    report={'offline_only':True,'hardware_accessed':False,'historical_shape_model_zero_gate_passed':False,'new_rig_or_metric_accuracy_accepted':False,'profiles':checks,'all_passed':True}
    (a.output/'summary.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
if __name__=='__main__':main()
