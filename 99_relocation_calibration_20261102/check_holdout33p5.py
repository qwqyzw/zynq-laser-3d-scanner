"""Independent Zynq-only check against the pinned 0/12mm candidate; never refit."""
from pathlib import Path
import json,hashlib,platform,sys
import cv2,numpy as np
cv2.setNumThreads(1)
P=Path('/home/xilinx/jupyter_notebooks/integrated_uart_20261020_193135/teammate_complete_20261020')
D=P/'99_relocation_calibration_20261102';F=D/'height33p5_capture_01';O=D/'laser_holdout_01';O.mkdir(exist_ok=False)
sys.path[:0]=[str(P/'04_runtime'),str(P/'01_fpga_baseline/reference')]
from geometric_calibration import pixel_rays
from geometry_core import intersect_rays
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
load=lambda p:json.loads(p.read_text())
PIN='4bdbc809e56a741e3dcef3def7343ae54c37e73d859b14729c6423baae26bb80'
report={'status':'checking','execution_role':'zynq','machine':platform.machine(),'holdout_height_mm':33.5,'holdout_used_for_fit':False,'geometry_deployed':False,'axis_validation_pending':True,'metric_accuracy_accepted':False,'motor_commands_sent':False,'limits':{'minimum_measured_columns':20,'maximum_plane_p95_mm':2.,'maximum_height_absolute_p95_mm':3.},'limits_source':'same rough preview holdout limits as frozen93/check_height33p5.py','exposure_selection_policy':'prefer5 if image quality passed and >=20 top-interior columns, otherwise20; do not choose based on height errors'}
try:
 assert platform.machine() in ['armv7l','aarch64']
 training=D/'laser_fit_01/report.json';assert sha(training)==PIN,'Training candidate changed'
 fit_report=load(training);assert fit_report['status']=='candidate_training_fit_passed_pending_33p5_holdout_and_axis'
 r=load(F/'report.json');assert r['status']=='static_actual_PL_capture_passed' and r['known_height_mm']==33.5
 assert r['diagnostics']['completed_frames']-r['baseline']['completed_frames']==6
 assert r['driver_healthy'] and r['quarantined_buffers']==0 and r['cleanup']['camera_release_confirmed'] and r['cleanup']['exposure_restoration_verified']
 camera_path=P/'94_current_geometry_20261024/current_geometry.json';assert sha(camera_path)==fit_report['prior_geometry_sha256']
 cam=load(camera_path)['camera'];fit=fit_report['fit'];n=np.asarray(fit_report['reference_plane']['normal']);offset=fit_report['reference_plane']['offset_mm'];ln=np.asarray(fit['normal']);ld=fit['offset_mm']
 ref=cv2.imread(str(D/'zero_capture_01/camera_session/bright_before_session/bright_01.png'),0);assert ref is not None
 rects=load(D/'height12_capture_01/support_check.json')['fixed_reference_checks'][0]['parts'];checks=[]
 for image in sorted((F/'camera_session').glob('bright_*_session/bright_*.png')):
  im=cv2.imread(str(image),0);parts=[]
  for rect in rects:
   x0,y0,x1,y1=rect['rect'];scores=cv2.matchTemplate(im[y0-8:y1+8,x0-8:x1+8],ref[y0:y1,x0:x1],cv2.TM_CCOEFF_NORMED)
   _,corr,_,loc=cv2.minMaxLoc(scores);x,y=loc;rest=scores.copy();rest[max(0,y-3):y+4,max(0,x-3):x+4]=-1
   margin=corr-float(rest.max());shift=float(np.hypot(x-8,y-8));parts.append({'rect':rect['rect'],'correlation':float(corr),'shift_px':shift,'peak_margin':margin,'passed':bool(corr>=.90 and shift<=2 and margin>=.02)})
  checks.append({'image':str(image.relative_to(D)),'sha256':sha(image),'parts':parts,'passed':all(v['passed'] for v in parts)})
 report['fixed_reference_checks']=checks
 assert len(checks)==6 and all(v['passed'] for v in checks),'Fixed base changed; preserve holdout, do not refit'
 polygon=np.asarray([[235,190],[375,198],[374,280],[224,271]],np.float32)
 report['top_mask_polygon_px']=polygon.tolist();report['mask_source_preview_sha256']=sha(D/'height33p5_preview_01/preview_01.png');report['mask_source']='visual interior of observed box top, selected before reading height errors'
 exposures=[]
 for exp in [5,20]:
  base=F/('exp_%d/images'%exp);pixels=np.loadtxt(base/'stripe_pixels.csv',delimiter=',',skiprows=1).reshape(-1,2)
  selected=np.asarray([v for v in pixels if cv2.pointPolygonTest(polygon,tuple(map(float,v)),True)>3],float).reshape(-1,2)
  quality=load(base/'stripe_quality.json');tx=load(base/'pl_transactions.json');assert len(tx)==3 and all(v.get('integer_reference_bit_exact') for v in tx)
  row={'exposure':exp,'quality_passed':bool(quality['image_quality_gate_passed']),'total_valid_columns':len(pixels),'top_columns':len(selected),'source_pixels_sha256':sha(base/'stripe_pixels.csv'),'integer_bit_exact_all_three_frames':True}
  if len(selected):
   rays=pixel_rays(selected,cam);known,good=intersect_rays(rays,n,offset-33.5);pred,good2=intersect_rays(rays,ln,ld);assert good.all() and good2.all()
   pe=known@ln+ld;height=pred@n+offset;error=height-33.5
   row.update(height_median_mm=float(np.median(height)),height_min_mm=float(height.min()),height_max_mm=float(height.max()),height_median_error_mm=float(np.median(error)),height_p95_absolute_error_mm=float(np.percentile(abs(error),95)),plane_p95_residual_mm=float(np.percentile(abs(pe),95)))
   np.savetxt(O/('independent_check_exp%d.csv'%exp),np.c_[selected,height,error,pe],delimiter=',',header='x_px,y_px,predicted_height_mm,height_error_mm,plane_residual_mm',comments='')
   image=cv2.imread(str(base/'raw_00.png'));cv2.polylines(image,[polygon.astype(np.int32)],True,(255,255,0),1)
   for x,y in selected:cv2.circle(image,(int(round(x)),int(round(y))),1,(0,255,0),-1)
   cv2.imwrite(str(O/('selected_exp%d.png'%exp)),image)
  exposures.append(row)
 report['exposures']=exposures
 chosen=next((v for v in exposures if v['quality_passed'] and v['top_columns']>=20),None);assert chosen is not None,'Not enough independently measured top columns'
 gates={'minimum20_top_columns':chosen['top_columns']>=20,'plane_p95_le2mm':chosen['plane_p95_residual_mm']<=2.,'height_absolute_p95_le3mm':chosen['height_p95_absolute_error_mm']<=3.,'all_four_fixed_references_passed':all(v['passed'] for v in checks),'training_frozen_unchanged':sha(training)==PIN,'old_geometry_unchanged':sha(camera_path)==fit_report['prior_geometry_sha256']}
 report.update(selected_exposure=chosen['exposure'],independent_check=chosen,gates=gates,training_report_sha256=PIN,source_capture_report_sha256=sha(F/'report.json'),status='independent33p5_holdout_passed_axis_pending' if all(gates.values()) else 'rejected')
except Exception as e:report.update(status='rejected',error=repr(e))
(O/'report.json').write_text(json.dumps(report,indent=2,allow_nan=False))
print(json.dumps({k:v for k,v in report.items() if k!='fixed_reference_checks'},indent=2))
if report.get('fixed_reference_checks'):print('Fixed reference extremes:',min(v['correlation'] for c in report['fixed_reference_checks'] for v in c['parts']),max(v['shift_px'] for c in report['fixed_reference_checks'] for v in c['parts']))
