"""Zynq-only current target fit. Predeclared six angles/48 corners train; all others check."""
from pathlib import Path
import sys,json,hashlib,platform,traceback,time
P=Path('/home/xilinx/jupyter_notebooks/integrated_uart_20261020_193135/teammate_complete_20261020');D=P/'99_relocation_calibration_20261102';O=D/'axis_fit_01';O.mkdir(exist_ok=False)
sys.path.insert(0,str(P/'04_runtime'))
import cv2,numpy as np
cv2.setNumThreads(1)
from geometric_calibration import object_points
from rotation_axis import fit_axis
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
load=lambda p:json.loads(p.read_text())
report={'status':'preparing','execution_role':'zynq','machine':platform.machine(),'hardware_accessed':False,'fit_angles_deg':[0,30,60,90,120,150],'old_axis_used_for_fit':False,'laser_used_for_axis_fit':False,'metric_accuracy_accepted':False,'geometry_deployed':False,'limits':{'pose_reprojection_rms_px':1.,'maximum_batch_deviation_px':.75,'maximum_axis_XYZ_rms_mm':1.5,'maximum_orientation_deg':1.,'maximum_held_angle_projection_rms_px':2.,'maximum_held_corner_projection_rms_px':1.,'maximum_LK_to_detection_p95_px':3.}}
def save(): (O/'report.json').write_text(json.dumps(report,indent=2,allow_nan=False))
try:
 assert platform.machine() in ['armv7l','aarch64']
 cap=load(D/'axis_capture_01_parent.json');assert cap['status']=='completed' and len(cap['points'])==37 and cap['fd_attempts']==72
 assert cap['cleanup']['motor_stop_confirmed'] and cap['cleanup']['camera_unowned'] and cap['cleanup']['unchanged_PL_idle']
 assert all(p['fixed_base']['passed'] and len(p['fixed_base']['patches'])==4 for p in cap['points'])
 old=load(P/'94_current_geometry_20261024/current_geometry.json');camera=old['camera'];K=np.array(camera['K']);dist=np.array(camera['dist_coeffs']);obj=object_points(camera['board']).astype(float)
 held=np.arange(72)[np.arange(72)%3==0];train=np.setdiff1d(np.arange(72),held);report.update(training_corner_indices=train.tolist(),withheld_corner_indices=held.tolist(),capture_sha256=sha(D/'axis_capture_01_parent.json'))
 previous_gray=previous_points=None;previous_angle=None;poses=[];observed_poses=[];angles=[];usable=[];reviews=[];excluded=[]
 def pnp(pixels,indices):
  ok,rv,tv=cv2.solvePnP(obj[indices],pixels[indices],K,dist,flags=cv2.SOLVEPNP_ITERATIVE);assert ok
  R=cv2.Rodrigues(rv)[0];t=tv.ravel();assert ((obj@R.T+t)[:,2]>0).all()
  errors=np.linalg.norm(cv2.projectPoints(obj,rv,tv,K,dist)[0].reshape(-1,2)-pixels,axis=1)
  return {'R':R.tolist(),'t_mm':t.tolist(),'pixels':pixels.tolist()},errors
 for point in cap['points']:
  angle=point['nominal_deg'];folder=D/'axis_capture_01'/point['folder'];mid=folder/'bright_01.png';gray=cv2.imread(str(mid),0);raw=np.asarray(point['corners_px'],np.float32).reshape(72,2)
  grids=[raw.reshape(8,9,2),raw.reshape(9,8,2).transpose(1,0,2)];choices=[q.reshape(72,2).copy() for g in grids for q in [g,g[::-1],g[:,::-1],g[::-1,::-1]]]
  label={'angle_deg':angle,'original_detection_preserved':True}
  if previous_points is None:chosen=choices[0]
  else:
   tracked,ok,_=cv2.calcOpticalFlowPyrLK(previous_gray,gray,previous_points.reshape(-1,1,2).astype(np.float32),None,winSize=(31,31),maxLevel=3,criteria=(cv2.TERM_CRITERIA_EPS|cv2.TERM_CRITERIA_COUNT,60,.001))
   back,backok,_=cv2.calcOpticalFlowPyrLK(gray,previous_gray,tracked,None,winSize=(31,31),maxLevel=3,criteria=(cv2.TERM_CRITERIA_EPS|cv2.TERM_CRITERIA_COUNT,60,.001))
   fb=np.linalg.norm(back.reshape(-1,2)-previous_points,axis=1);valid=(ok.ravel()!=0)&(backok.ravel()!=0)&(fb<=1.)
   label.update(LK_valid_corners=int(valid.sum()),previous_usable_angle_deg=previous_angle)
   if valid.sum()<60:
    excluded.append({**label,'reason':'Less than60 forward/backward-consistent observed corners'});continue
   costs=[float(np.percentile(np.linalg.norm(v[valid]-tracked.reshape(-1,2)[valid],axis=1),95)) for v in choices];index=int(np.argmin(costs));chosen=choices[index];label.update(symmetry_choice=index,LK_detection_p95_px=costs[index],all_symmetry_costs=costs)
   if costs[index]>3.:
    excluded.append({**label,'reason':'SB detection inconsistent with measured optical flow; not replaced by guessed points'});continue
  batches=[]
  for frame in point['camera']['frames']:
   path=folder/frame['file'];assert sha(path)==frame['sha256']
   image=cv2.imread(str(path),0);found=cv2.cornerSubPix(image,chosen.reshape(-1,1,2).copy(),(3,3),(-1,-1),(cv2.TERM_CRITERIA_EPS|cv2.TERM_CRITERIA_MAX_ITER,50,.001)).reshape(-1,2);batches.append(found)
  pixels=np.mean(batches,axis=0);span=float(np.linalg.norm(np.asarray(batches)-pixels[None,:,:],axis=2).max())
  pose,errors=pnp(pixels,train);obs,allerr=pnp(pixels,np.arange(72));fit_rms=float(np.sqrt(np.mean(errors[train]**2)));held_rms=float(np.sqrt(np.mean(errors[held]**2)))
  label.update(max_batch_deviation_px=span,training_PnP_rms_px=fit_rms,held_corner_PnP_rms_px=held_rms,all72_PnP_rms_px=float(np.sqrt(np.mean(allerr**2))))
  if span>.75 or max(fit_rms,held_rms)>1.:
   excluded.append({**label,'reason':'Observed pose or batch stability rejected'});continue
  poses.append(pose);observed_poses.append(obs);usable.append(point);angles.append(point['relative_feedback_deg']);reviews.append(label);previous_gray=gray;previous_points=pixels;previous_angle=angle
 report.update(usable_angles_deg=[p['nominal_deg'] for p in usable],excluded_detections=excluded,frame_reviews=reviews);save()
 assert len(usable)>=31 and usable[0]['nominal_deg']==0 and usable[-1]['nominal_deg']==360,'Insufficient usable full-circle target observations'
 fit_indices=[i for i,p in enumerate(usable) if p['nominal_deg'] in report['fit_angles_deg']];assert len(fit_indices)==6,'Missing predeclared training angle'
 initial=fit_axis(poses,angles,camera['board'],fit_indices);R0=np.array(initial['initial_board_R']);t0=np.array(initial['initial_board_t_mm']);A=np.array(initial['axis_direction_camera']);C=np.array(initial['axis_point_on_initial_paper_mm']);assert A[2]<-.5
 p0=np.r_[cv2.Rodrigues(R0)[0].ravel(),t0,A[:2]/(-A[2]),(R0.T@(C-t0))[:2]];scale=np.array([.01]*3+[1.]*3+[.01]*2+[1.]*2)
 def decode(q):
  p=p0+np.asarray(q)*scale;r=cv2.Rodrigues(p[:3])[0];t=p[3:6];a=np.r_[p[6:8],-1.];a/=np.linalg.norm(a);c=r@np.r_[p[8:10],0.]+t;return r,t,a,c
 def projection(q,i):
  r,t,a,c=decode(q);Q=cv2.Rodrigues(a*np.radians(angles[i]))[0];return cv2.projectPoints(obj,cv2.Rodrigues(Q@r)[0],Q@(t-c)+c,K,dist)[0].reshape(-1,2)
 def objective(q):return np.concatenate([(projection(q,i)[train]-np.asarray(poses[i]['pixels'])[train]).ravel() for i in fit_indices])
 began=time.monotonic()
 try:
  from scipy.optimize import least_squares
  result=least_squares(objective,np.zeros(10),method='lm',max_nfev=1500,ftol=1e-10,xtol=1e-10,gtol=1e-10);q=result.x;optimizer={'name':'LM','success':bool(result.success),'evaluations':int(result.nfev)}
 except ImportError:
  q=np.zeros(10);damping=1e-3;accepted=0
  for iteration in range(120):
   res=objective(q);step=1e-4;J=np.column_stack([(objective(q+np.eye(10)[k]*step)-objective(q-np.eye(10)[k]*step))/(2*step) for k in range(10)]);H=J.T@J;delta=np.linalg.solve(H+damping*np.diag(np.maximum(np.diag(H),1e-9)),-J.T@res)
   if np.linalg.norm(delta)<1e-7:break
   if np.sum(objective(q+delta)**2)<np.sum(res**2):q+=delta;damping=max(1e-9,damping/3);accepted+=1
   else:damping=min(1e12,damping*10)
  optimizer={'name':'bounded damped GN','success':accepted>0,'iterations':iteration+1}
 r,t,a,c=decode(q);bx=r[:,0]-a*(a@r[:,0]);bx/=np.linalg.norm(bx);basis=np.column_stack([bx,np.cross(a,bx),a]);n=r[:,2];offset=-float(n@t)
 if n@t>0:n=-n;offset=-offset
 residuals=[]
 for i,pose in enumerate(observed_poses):
  Q=cv2.Rodrigues(a*np.radians(angles[i]))[0];pred=(obj@r.T+t-c)@Q.T+c;observed=obj@np.array(pose['R']).T+pose['t_mm'];xe=np.linalg.norm(pred-observed,axis=1);pe=np.linalg.norm(projection(q,i)-np.asarray(pose['pixels']),axis=1);orient=float(np.degrees(np.linalg.norm(cv2.Rodrigues(np.asarray(pose['R'])@(Q@r).T)[0])))
  residuals.append({'nominal_deg':usable[i]['nominal_deg'],'used_for_fit':i in fit_indices,'corner_xyz_rms_mm':float(np.sqrt(np.mean(xe**2))),'orientation_error_deg':orient,'pixel_rms_px':float(np.sqrt(np.mean(pe**2))),'heldout_corner_pixel_rms_px':float(np.sqrt(np.mean(pe[held]**2)))})
 gates={'optimizer_converged':optimizer['success'],'all_full_angle_fixed_markers_passed':True,'all_observed_XYZ_RMS_le1p5mm':all(v['corner_xyz_rms_mm']<=1.5 for v in residuals),'all_orientation_error_le1deg':all(v['orientation_error_deg']<=1 for v in residuals),'heldout_angle_pixel_RMS_le2px':all(v['pixel_rms_px']<=2 for v in residuals if not v['used_for_fit']),'all_heldout_corner_pixel_RMS_le1px':all(v['heldout_corner_pixel_rms_px']<=1 for v in residuals),'training_pixel_RMS_le1px':all(v['pixel_rms_px']<=1 for v in residuals if v['used_for_fit']),'centre_condition_le20':initial['centre_condition_number']<=20}
 assert np.allclose(basis.T@basis,np.eye(3),atol=1e-7) and np.isclose(np.linalg.det(basis),1)
 axis={**initial,'axis_direction_camera':a.tolist(),'axis_point_on_initial_paper_mm':c.tolist(),'initial_board_R':r.tolist(),'initial_board_t_mm':t.tolist(),'turntable_basis_camera_columns':basis.tolist(),'reference_plane':{'normal':n.tolist(),'offset_mm':offset},'motor_zero_reference_deg':cap['points'][0]['feedback_before_deg'],'fit_nominal_angles_deg':report['fit_angles_deg'],'holdout_nominal_angles_deg':[p['nominal_deg'] for i,p in enumerate(usable) if i not in fit_indices],'residuals':residuals,'gates':gates,'candidate_checks_passed':all(gates.values()),'large_angle_axis_validation_passed':all(gates.values()),'source_capture_sha256':report['capture_sha256'],'metric_accuracy_accepted':False,'fit_algorithm':'joint target projection using only six predefined angles and48 training corners; all72 three-frame-mean PnP used only for pose validation'}
 report.update(status='axis_candidate_passed' if all(gates.values()) else 'axis_candidate_rejected',optimizer=optimizer,optimization_seconds=time.monotonic()-began,gates=gates,residuals=residuals,max_XYZ_RMS_mm=max(v['corner_xyz_rms_mm'] for v in residuals),max_orientation_deg=max(v['orientation_error_deg'] for v in residuals),max_pixel_RMS_px=max(v['pixel_rms_px'] for v in residuals),axis_refitted=True,training_objective_uses_holdout=False)
 (O/'axis_candidate.json').write_text(json.dumps(axis,indent=2,allow_nan=False));save()
except Exception as e:report.update(status='failed',error=repr(e),traceback=traceback.format_exc());save()
print(json.dumps({k:v for k,v in report.items() if k not in ['frame_reviews','residuals','excluded_detections']},indent=2));print('Excluded observations:',report.get('excluded_detections',[]))
