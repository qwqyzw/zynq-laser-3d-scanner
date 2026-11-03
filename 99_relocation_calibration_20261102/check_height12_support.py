"""File-only validation of current 12mm calibration evidence on Zynq."""
from pathlib import Path
import json,hashlib,platform
import cv2,numpy as np
cv2.setNumThreads(1)
P=Path('/home/xilinx/jupyter_notebooks/integrated_uart_20261020_193135/teammate_complete_20261020')
D=P/'99_relocation_calibration_20261102';F=D/'height12_capture_01';Z=D/'zero_capture_01'
load=lambda p:json.loads(p.read_text())
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
r=load(F/'report.json');assert r['status']=='static_actual_PL_capture_passed'
assert r['diagnostics']['completed_frames']-r['baseline']['completed_frames']==6
assert r['driver_healthy'] and r['quarantined_buffers']==0 and r['cleanup']['camera_release_confirmed'] and r['cleanup']['exposure_restoration_verified']
ref=cv2.imread(str(Z/'camera_session/bright_before_session/bright_01.png'),0);assert ref is not None
rects=[(140,184,172,212),(447,195,477,222),(85,319,123,351),(476,346,513,376)]
checks=[]
for phase in ['bright_before_session','bright_after_session']:
 for image in sorted((F/'camera_session'/phase).glob('bright_*.png')):
  im=cv2.imread(str(image),0);parts=[]
  for x0,y0,x1,y1 in rects:
   scores=cv2.matchTemplate(im[y0-8:y1+8,x0-8:x1+8],ref[y0:y1,x0:x1],cv2.TM_CCOEFF_NORMED)
   _,corr,_,loc=cv2.minMaxLoc(scores);x,y=loc;rest=scores.copy();rest[max(0,y-3):y+4,max(0,x-3):x+4]=-1
   margin=corr-float(rest.max());shift=float(np.hypot(x-8,y-8))
   parts.append({'rect':list((x0,y0,x1,y1)),'correlation':float(corr),'shift_px':shift,'peak_margin':margin,'passed':bool(corr>=.90 and shift<=2 and margin>=.02)})
  checks.append({'image':str(image.relative_to(D)),'sha256':sha(image),'parts':parts,'passed':all(v['passed'] for v in parts)})
polygon=np.asarray([[273,244],[406,251],[405,291],[272,286]],np.float32)
support=[]
for exp in [5,20]:
 base=F/('exp_%d/images'%exp);pixels=np.loadtxt(base/'stripe_pixels.csv',delimiter=',',skiprows=1).reshape(-1,2)
 selected=np.asarray([v for v in pixels if cv2.pointPolygonTest(polygon,tuple(map(float,v)),True)>3],float).reshape(-1,2)
 quality=load(base/'stripe_quality.json');transactions=load(base/'pl_transactions.json')
 assert len(transactions)==3 and all(v.get('integer_reference_bit_exact') is True for v in transactions)
 row={'exposure':exp,'quality_passed':quality['image_quality_gate_passed'],'total_valid_columns':len(pixels),'top_interior_columns':len(selected),'original_PL_coordinates':True,'integer_bit_exact_all_three_frames':True,'pixels_sha256':sha(base/'stripe_pixels.csv')}
 np.savetxt(F/('height12_top_pixels_exp%d.csv'%exp),selected,delimiter=',',header='x_px,y_px',comments='')
 image=cv2.imread(str(base/'raw_00.png'));cv2.polylines(image,[polygon.astype(np.int32)],True,(255,255,0),1)
 for x,y in selected:cv2.circle(image,(int(round(x)),int(round(y))),1,(0,255,0),-1)
 cv2.imwrite(str(F/('top_support_exp%d.png'%exp)),image)
 support.append(row)
out={'execution_role':'zynq','machine':platform.machine(),'known_height_mm':12,'top_mask_polygon_px':polygon.tolist(),'mask_source_preview_sha256':sha(D/'height12_preview_02/preview_01.png'),'mask_source':'visually selected interior of actual white flat top, no fitted coordinates used','fixed_reference_checks':checks,'all_fixed_reference_checks_passed':all(v['passed'] for v in checks),'exposure_support':support,'geometry_deployed':False,'height_accuracy_validated':False,'motor_commands_sent':False}
out['status']='height12_evidence_passed_pending_fit_and_holdout' if out['all_fixed_reference_checks_passed'] and any(v['quality_passed'] and v['top_interior_columns']>=20 for v in support) else 'needs_review'
(F/'support_check.json').write_text(json.dumps(out,indent=2))
print(json.dumps({k:v for k,v in out.items() if k!='fixed_reference_checks'},indent=2))
print('Reference check extremes:',min(v['correlation'] for c in checks for v in c['parts']),max(v['shift_px'] for c in checks for v in c['parts']))
