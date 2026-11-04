"""Measured central component and independently angle-supported extra points.

No coordinate changes. Raw candidates remain separately available for diagnosis.
"""
from pathlib import Path
import json,hashlib
import numpy as np
from scipy.spatial import cKDTree

def select_foreground(points,groups,radii,baseline):
 p=np.asarray(points,float);g=np.asarray(groups,int);r=np.asarray(radii,float);b=np.asarray(baseline,bool)
 if p.ndim!=2 or p.shape[1]!=3 or any(a.shape!=(len(p),) for a in [g,r,b]) or not np.isfinite(p).all():raise ValueError('Invalid measured foreground input')
 indices=np.flatnonzero(b)
 if len(indices)<30:raise ValueError('Too few stable baseline object candidates')
 parent=np.arange(len(indices))
 def find(x):
  while parent[x]!=x:parent[x]=parent[parent[x]];x=parent[x]
  return x
 for a,c in cKDTree(p[indices]).query_pairs(3.):
  aa,cc=find(a),find(c)
  if aa!=cc:parent[aa]=cc
 components={}
 for j,i in enumerate(indices):components.setdefault(find(j),[]).append(int(i))
 eligible=[np.asarray(ids,int) for ids in components.values() if len(ids)>=30 and len(np.unique(g[ids]))>=6]
 if not eligible:raise ValueError('No angle-supported central object component')
 central=min(eligible,key=lambda ids:np.median(r[ids]));keep=np.zeros(len(p),bool);keep[central]=True
 extra=np.flatnonzero(~b)
 for i,near in zip(extra,cKDTree(p[central]).query_ball_point(p[extra],3.)):
  if len({int(g[central[j]]) for j in near if g[central[j]]!=g[i]})>=2:keep[i]=True
 report={'raw_candidates':len(p),'raw_min8_baseline_candidates':len(indices),'central_baseline_points':len(central),'baseline_background_excluded_from_object':len(indices)-len(central),
  'accepted_extra_measured_points':int(keep[extra].sum()),'extra_background_rejected':int((~keep[extra]).sum()),'foreground_points':int(keep.sum()),'background_candidates_excluded':int((~keep).sum()),
  'all_central_baseline_points_retained':True,'raw_coordinates_changed':False,'new_measured_points_added':0,'height_or_shape_template_used':False,
  'component_link_mm':3.,'minimum_component_points':30,'minimum_component_angles':6,'candidate_max_distance_mm':3.,'candidate_minimum_other_angles':2,
  'assumption':'single connected object placed at turntable centre; disconnected background remains in raw export','central_baseline_rows':central.tolist(),'accepted_rows':np.flatnonzero(keep).tolist()}
 return keep,report

def baseline_mask(root,rows):
 """Fast85 provenance CSV, or exact CPU-only baseline recovery for existing81."""
 import csv,sys
 root=Path(root);P=Path(__file__).resolve().parent.parent;trial=json.loads((root/'trial.json').read_text());geometry=P/'61_current_axis_laser_release_20261024/current_geometry.json'
 if hashlib.sha256(geometry.read_bytes()).hexdigest()!=trial['geometry_sha256']:raise ValueError('Input geometry changed')
 keys={(int(r['point_index']),int(float(r['x_px'])),int(r['source_exposure'])):i for i,r in enumerate(rows)};mask=np.zeros(len(rows),bool)
 fast=root/'model/baseline_min8_points.csv'
 if fast.is_file():
  if hashlib.sha256(fast.read_bytes()).hexdigest()!=trial.get('baseline_min8_csv_sha256'):raise ValueError('Fast baseline provenance differs')
  for r in csv.DictReader(fast.open()):
   i=keys[(int(r['point_index']),int(float(r['x_px'])),int(r['source_exposure']))]
   if any(float(rows[i][k])!=float(r[k]) for k in ['y_px','x_mm','y_mm','z_mm']):raise ValueError('Baseline point is not exact input')
   mask[i]=True
  return mask,'bound_baseline_CSV_no_image_replay'
 if trial['version']!='20261024.core_stripe_maintenance.v1':raise ValueError('No known measured baseline provenance')
 sys.path[:0]=[str(P/'81_core_maintenance_fix_20261024'),str(P/'61_current_axis_laser_release_20261024'),str(P/'04_runtime'),str(P/'09_tools')]
 import cv2
 from laser_vertical_fast import keep_long_runs
 from temporal_consensus import review_from_stripes_fast
 from fuse_measured_profiles import _fuse_temporal
 import fast_reconstruct_on_zynq as recon
 cv2.setNumThreads(1);mods=recon.modules(P);model=json.loads(geometry.read_text())
 for pose in trial['poses']:
  parts={}
  for e in pose['exposures']:
   folder=root/e['evidence'];images=[];stripes=[]
   for i in range(3):
    images.append(cv2.imread(str(folder/('raw_%02d.png'%i))));words=np.fromfile(folder/('pl_%02d_actual.bin'%i),dtype='<u4');valid=keep_long_runs(words>>31!=0,8);stripes.append({'valid':valid,'y':np.where(valid,(words&0x3ffff)/256.,np.nan)})
   px,q=review_from_stripes_fast(images,stripes,(175,210,440,340));profile=recon.reconstruct_profile(px,e['before_deg'],model,mods,diagnostic_preview=True)
   parts[e['exposure']]={'pixels':px,'selected':profile['selected'],'quality_gate_passed':q['image_quality_gate_passed'],'temporal_columns':[s['x'] for s in q.get('two_frame_support',[])]}
  fused,_=_fuse_temporal(parts[5],parts[20])
  if len(fused)!=pose['fusion']['old_min_run8_points']:raise ValueError('Measured baseline count differs')
  for f in fused:
   i=keys[(pose['index'],f['x_px'],f['source_exposure'])]
   if float(rows[i]['y_px'])!=f['y_px']:raise ValueError('Baseline coordinate differs')
   mask[i]=True
 return mask,'existing81_original_image_CPU_quality_replay_no_hardware'
