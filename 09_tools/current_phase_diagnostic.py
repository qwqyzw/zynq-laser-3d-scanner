"""Offline checkerboard/axis agreement diagnostic, not automatic acceptance."""
from pathlib import Path
import argparse,sys,json
import cv2,numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'04_runtime'))
from rotation_axis import pose_candidates,rotation
from geometric_calibration import object_points

def main():
    ap=argparse.ArgumentParser();ap.add_argument('images',nargs=3,type=Path);ap.add_argument('--output',required=True,type=Path);a=ap.parse_args()
    models=ROOT/'05_geometry/shape180_20261018'
    camera=json.loads((models/'camera_reused.json').read_text());axis=json.loads((models/'rotation_axis_current.json').read_text())
    R0=np.array(axis['initial_board_R']);t0=np.array(axis['initial_board_t_mm']);n=np.array(axis['axis_direction_camera']);o=np.array(axis['axis_point_on_initial_paper_mm'])
    obj=object_points(camera['board']);xyz0=obj@R0.T+t0;rows=[]
    for path in a.images:
        image=cv2.imread(str(path))
        if image is None or image.shape!=(480,640,3):raise ValueError('Need original640x480 bright checkerboard image')
        values=[]
        for p in pose_candidates(image,camera):
            R=np.array(p['R']);t=np.array(p['t_mm']);a_deg=float(np.degrees(cv2.Rodrigues(R@R0.T)[0].ravel()@n))
            Q=rotation(n*np.radians(a_deg));pred=(xyz0-o)@Q.T+o;observed=obj@R.T+t
            distances=np.linalg.norm(pred-observed,axis=1);orientation=float(np.degrees(np.linalg.norm(cv2.Rodrigues(R@(Q@R0).T)[0])))
            values.append({'axis_angle_candidate_deg':a_deg,'corner_xyz_rms_mm':float(np.sqrt(np.mean(distances**2))),'orientation_error_deg':orientation,'candidate_numeric_gates_passed':bool(np.sqrt(np.mean(distances**2))<=.75 and orientation<=1)})
        rows.append({'image':str(path),'candidates':values})
    report={'kind':'diagnostic_only','hardware_accessed':False,'automatic_phase_selected':False,'new_rig_geometry_or_height_accuracy_accepted':False,'requires_checkerboard_orientation_and_current_encoder_evidence':True,'frames':rows}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
if __name__=='__main__':main()
