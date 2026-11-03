"""Zynq file-only 0/12mm training fit; no 33.5mm input, no deployed changes."""
from pathlib import Path
import sys,json,hashlib,platform
P=Path('/home/xilinx/jupyter_notebooks/integrated_uart_20261020_193135/teammate_complete_20261020')
D=P/'99_relocation_calibration_20261102';O=D/'laser_fit_01';O.mkdir(exist_ok=False)
sys.path[:0]=[str(P/'04_runtime'),str(P/'01_fpga_baseline/reference')]
import cv2,numpy as np
cv2.setNumThreads(1)
from geometric_calibration import object_points,pixel_rays
from geometry_core import intersect_rays,fit_plane,reconstruct
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
load=lambda p:json.loads(p.read_text())
report={'status':'checking','execution_role':'zynq','machine':platform.machine(),'fit_heights_mm':[0,12],'holdout_height_mm':33.5,'holdout_used_for_fit':False,'geometry_deployed':False,'metric_accuracy_accepted':False,'axis_validation_pending':True,'motor_commands_sent':False,'exposure_selection_policy':'prefer exposure5 when quality passed and >=20 image-mask interior measured columns; otherwise20; decided before residual fitting'}
try:
 assert platform.machine() in ['armv7l','aarch64']
 camera_path=P/'94_current_geometry_20261024/current_geometry.json';old=load(camera_path);camera=old['camera'];report['prior_geometry_sha256']=sha(camera_path)
 preview=D/'empty_preview_01';detection=load(preview/'preview_detection.json');assert detection['detected_full_checkerboard'] and detection['corner_count']==72
 image_path=preview/'preview_01.png';gray=cv2.imread(str(image_path),0);found=np.asarray(detection['corners_px'],np.float32).reshape(-1,1,2)
 found=cv2.cornerSubPix(gray,found,(3,3),(-1,-1),(cv2.TERM_CRITERIA_EPS|cv2.TERM_CRITERIA_MAX_ITER,50,.001))
 obj=object_points(camera['board']).astype(float);K=np.asarray(camera['K']);dist=np.asarray(camera['dist_coeffs'])
 ok,rv,tv=cv2.solvePnP(obj,found.astype(float),K,dist,flags=cv2.SOLVEPNP_ITERATIVE);assert ok
 R=cv2.Rodrigues(rv)[0];n=R[:,2];t=tv.ravel()
 if n@t>0:n=-n
 offset=-float(n@t)
 projected=cv2.projectPoints(obj,rv,tv,K,dist)[0].reshape(-1,2);reproj=float(np.sqrt(np.mean(np.sum((projected-found.reshape(-1,2))**2,axis=1))))
 report['reference_plane']={'normal':n.tolist(),'offset_mm':offset};report['reference_pose']={'R':R.tolist(),'t_mm':t.tolist(),'reprojection_rms_px':reproj,'corners_px':found.reshape(-1,2).tolist(),'source_image_sha256':sha(image_path),'camera_intrinsics_reused':True,'full_intrinsic_recalibration_performed':False}
 assert reproj<=1.,'Current camera pose reprojection exceeds1px'
 support=load(D/'height12_capture_01/support_check.json');assert support['status']=='height12_evidence_passed_pending_fit_and_holdout'
 groups=[];selected_groups=[];evidence=[]
 for label,height in [('zero',0.),('height12',12.)]:
  F=D/(label+'_capture_01');r=load(F/'report.json');assert r['status']=='static_actual_PL_capture_passed' and r['driver_healthy'] and r['quarantined_buffers']==0
  assert r['cleanup']['camera_release_confirmed'] and r['cleanup']['exposure_restoration_verified']
  polygon=cv2.convexHull(found).reshape(-1,2) if label=='zero' else np.asarray(support['top_mask_polygon_px'],np.float32)
  options=[]
  for exp in [5,20]:
   base=F/('exp_%d/images'%exp);quality=load(base/'stripe_quality.json');transactions=load(base/'pl_transactions.json')
   assert len(transactions)==3 and all(v['integer_reference_bit_exact'] for v in transactions)
   pixels=np.loadtxt(base/'stripe_pixels.csv',delimiter=',',skiprows=1).reshape(-1,2)
   selected=np.asarray([v for v in pixels if cv2.pointPolygonTest(polygon,tuple(map(float,v)),True)>3],float).reshape(-1,2)
   options.append({'exp':exp,'selected':selected,'path':base/'stripe_pixels.csv','quality':quality})
  chosen=next((v for v in options if v['quality']['image_quality_gate_passed'] and len(v['selected'])>=20),None);assert chosen is not None,'Insufficient masked top measurements'
  selected=chosen['selected'];xyz,good=intersect_rays(pixel_rays(selected,camera),n,offset-height);assert good.all()
  groups.append(xyz);selected_groups.append((height,selected));evidence.append({'label':label,'known_height_mm':height,'exposure':chosen['exp'],'selected_columns':len(selected),'pixels_sha256':sha(chosen['path']),'report_sha256':sha(F/'report.json'),'mask_polygon_px':polygon.tolist()})
  np.savetxt(O/(label+'_selected_pixels.csv'),selected,delimiter=',',header='x_px,y_px',comments='')
  np.savetxt(O/(label+'_camera_points.csv'),xyz,delimiter=',',header='x_camera_mm,y_camera_mm,z_camera_mm',comments='')
 fit=fit_plane(np.concatenate(groups),min_second_spread_mm=2.)
 report.update(fit=fit,evidence=evidence)
 assert fit['rms_mm']<=.5,'Training residual exceeds0.5mm; do not deploy'
 stats=[]
 for height,pixels in selected_groups:
  _,h,good=reconstruct(pixel_rays(pixels,camera),fit,report['reference_plane']);assert good.all()
  stats.append({'known_height_mm':height,'columns':len(pixels),'training_reconstructed_median_mm':float(np.median(h)),'training_absolute_error_p95_mm':float(np.percentile(np.abs(h-height),95))})
 report['training_checks_not_independent_accuracy_validation']=stats
 assert sha(camera_path)==report['prior_geometry_sha256']
 report['status']='candidate_training_fit_passed_pending_33p5_holdout_and_axis'
except Exception as e:report.update(status='rejected',error=repr(e))
(O/'report.json').write_text(json.dumps(report,indent=2,allow_nan=False))
print(json.dumps({k:v for k,v in report.items() if k!='reference_pose'},indent=2))
if 'reference_pose' in report:print('Checkerboard reprojection RMS:',report['reference_pose']['reprojection_rms_px'])
assert report['status']!='rejected'
