"""User-authorized hexagonal prism completion, computed only on Zynq for real data.
Measured input is never moved. Six vertical planes and the bottom are estimates.
"""
import numpy as np
from scipy.spatial import ConvexHull,cKDTree

def edge_distances(xy,poly):
 a=poly;b=np.roll(poly,-1,axis=0);v=b-a;u=xy[:,None,:]-a[None,:,:]
 t=np.clip(np.einsum('nki,ki->nk',u,v)/np.sum(v*v,axis=1),0,1)
 return np.linalg.norm(u-t[:,:,None]*v[None,:,:],axis=2)

def area(poly):return .5*float(np.sum(poly[:,0]*np.roll(poly[:,1],-1)-poly[:,1]*np.roll(poly[:,0],-1)))

def footprint(top,groups):
 boundary=[];bg=[]
 for group in np.unique(groups):
  q=top[groups==group,:2]
  if len(q)<4:continue
  _,_,v=np.linalg.svd(q-q.mean(axis=0),full_matrices=False);along=q@v[0]
  if np.ptp(along)<2:continue
  for j in [np.argmin(along),np.argmax(along)]:boundary.append(q[j]);bg.append(int(group))
 boundary=np.asarray(boundary);bg=np.asarray(bg)
 if len(boundary)<16 or len(np.unique(bg))<6:raise ValueError('Insufficient measured top boundary angles')
 poly=boundary[ConvexHull(boundary).vertices]
 if len(poly)<6:raise ValueError('Measured footprint has fewer than six resolvable vertices')
 while len(poly)>6:
  prev=np.roll(poly,1,axis=0);nxt=np.roll(poly,-1,axis=0)
  lost=np.abs(np.cross(poly-prev,nxt-poly));poly=np.delete(poly,int(np.argmin(lost)),axis=0)
 if area(poly)<0:poly=poly[::-1]
 initial=poly.copy();assign=edge_distances(boundary,poly).argmin(axis=1);normals=[];offsets=[];support=[]
 for i in range(6):
  a,b=poly[i],poly[(i+1)%6];v=b-a;old=np.array([v[1],-v[0]])/np.linalg.norm(v);oldc=float(old@a)
  mask=(assign==i)&(edge_distances(boundary,poly)[:,i]<=2.0);q=boundary[mask];n=old;c=oldc;refit=False
  if len(q)>=4 and len(np.unique(bg[mask]))>=2:
   weights=np.ones(len(q))
   for _ in range(5):
    centre=np.average(q,axis=0,weights=weights);_,_,vh=np.linalg.svd((q-centre)*np.sqrt(weights[:,None]),full_matrices=False);candidate=vh[-1]
    if candidate@old<0:candidate=-candidate
    candidate_c=float(np.median(q@candidate));res=np.abs(q@candidate-candidate_c);weights=1/np.maximum(1,res/.5)
   if candidate@old>=np.cos(np.deg2rad(20)) and abs(candidate_c-candidate@a)<=1.5:
    n=candidate;c=candidate_c;refit=True
  normals.append(n);offsets.append(c);support.append({'edge':i,'measured_boundary_points':int(mask.sum()),'measured_angle_groups':int(len(np.unique(bg[mask]))),'line_refitted':refit})
 try:
  refined=np.array([np.linalg.solve(np.array([normals[(i-1)%6],normals[i]]),[offsets[(i-1)%6],offsets[i]]) for i in range(6)])
  turns=np.cross(np.roll(refined,-1,axis=0)-refined,np.roll(refined,-2,axis=0)-np.roll(refined,-1,axis=0))
  if np.any(turns<=1e-6) or area(refined)<=0 or np.max(np.linalg.norm(refined-initial,axis=1))>3:raise ValueError('Unstable edge intersection')
  poly=refined
 except (np.linalg.LinAlgError,ValueError):poly=initial
 lengths=np.linalg.norm(np.roll(poly,-1,axis=0)-poly,axis=1)
 if lengths.min()<.5 or lengths.max()/lengths.min()>8:raise ValueError('Hexagonal footprint is degenerate')
 errors=edge_distances(boundary,poly).min(axis=1)
 if np.percentile(errors,95)>3:raise ValueError('Measured boundary does not support six-plane display fit')
 return poly,{'boundary_points':len(boundary),'boundary_angle_groups':int(len(np.unique(bg))),'boundary_fit_median_mm':float(np.median(errors)),'boundary_fit_p95_mm':float(np.percentile(errors,95)),'side_support':support,'regular_hexagon_assumed':False}

def top_band(points,groups):
 z=points[:,2];lo,hi=np.percentile(z,[2,99]);centres=np.arange(max(lo,np.percentile(z,45)),hi+.5,.5);options=[]
 for c in centres:
  mask=np.abs(z-c)<=1.25;ng=len(np.unique(groups[mask]));n=int(mask.sum())
  if n>=30 and ng>=6:options.append((n,c,mask))
 if not options:raise ValueError('No sufficiently supported flat top height band')
 maximum=max(o[0] for o in options);candidates=[o for o in options if o[0]>=maximum*.85];_,_,mask=max(candidates,key=lambda o:o[1])
 height=float(np.median(z[mask]));mask=np.abs(z-height)<=1.5
 if mask.sum()<30 or mask.mean()<.2 or height<=3:raise ValueError('Insufficient measured top support for extrusion')
 xy=points[mask,:2];sv=np.linalg.svd(xy-xy.mean(axis=0),compute_uv=False)
 if sv[1]<2 or sv[1]/sv[0]<.15:raise ValueError('Top footprint is nearly collinear')
 return mask,height

def estimate(points,groups,base_z=0.):
 original=np.array(points,copy=True);points=np.asarray(points,float);groups=np.asarray(groups,int)
 if points.ndim!=2 or points.shape[1]!=3 or not 60<=len(points)<=50000 or groups.shape!=(len(points),) or not np.isfinite(points).all():raise ValueError('Need60..50000 finite measured XYZ samples and angle groups')
 if base_z!=0:raise ValueError('This display proposal uses the calibrated table-origin base z=0')
 distances,_=cKDTree(points).query(points,k=min(9,len(points)));scores=distances[:,1:].mean(axis=1);med=np.median(scores);mad=np.median(abs(scores-med));keep=scores<=med+4*max(1.4826*mad,med*.05,1e-6)
 if keep.mean()<.95:keep[:]=True
 cleaned=points[keep];cg=groups[keep];top,height=top_band(cleaned,cg);poly,fit=footprint(cleaned[top],cg[top]);top_residual=np.abs(cleaned[top,2]-height)
 # Hold out complete angle groups; they do not choose validation height or outline.
 heldout=(cg%5)==0;train_top,train_height=top_band(cleaned[~heldout],cg[~heldout]);train_poly,_=footprint(cleaned[~heldout][train_top],cg[~heldout][train_top]);evaluation=heldout&top
 if evaluation.sum()<10:raise ValueError('Insufficient held-out top samples')
 err=np.abs(cleaned[evaluation,2]-train_height);outside=[]
 for a,b in zip(train_poly,np.roll(train_poly,-1,axis=0)):
  v=b-a;n=np.array([v[1],-v[0]])/np.linalg.norm(v);outside.append(np.maximum(cleaned[evaluation,:2]@n-n@a,0))
 outside=np.max(outside,axis=0);display_ok=bool(np.percentile(err,95)<=2.5 and np.percentile(outside,95)<=3)
 if not display_ok:raise ValueError('Held-out angle samples do not support the proposed prism')
 vertices=np.vstack([np.c_[poly,np.full(6,base_z)],np.c_[poly,np.full(6,height)]]);faces=[];kinds=[]
 for i in range(1,5):faces.append([6,6+i,6+i+1]);kinds.append('estimated_top')
 for i in range(1,5):faces.append([0,i+1,i]);kinds.append('assumed_bottom')
 for i in range(6):
  j=(i+1)%6;faces.extend([[i,j,j+6],[i,j+6,i+6]]);kinds.extend(['inferred_vertical_side']*2)
 faces=np.asarray(faces,int);edges=np.sort(np.concatenate([faces[:,[0,1]],faces[:,[1,2]],faces[:,[2,0]]]),axis=1);_,counts=np.unique(edges,axis=0,return_counts=True)
 assert np.array_equal(original,points) and (counts==2).all()
 stats={'raw_points':len(points),'foreground_points':len(points),'denoised_points':len(cleaned),'statistical_outliers':int((~keep).sum()),'mesh_vertices':12,'mesh_faces':20,'boundary_edges':0,'watertight':True,'closed_for_display_only':True,'surface_method':'measured_top_footprint_vertical_hex_prism_completion','shape_completion':True,'shape_prior':'user_requested_six_sided_prism_with_vertical_walls','regular_hexagon_assumed':False,'top_height_table_frame_mm':height,'base_height_assumed_mm':base_z,'side_plane_count':6,'top_measured_points':int(top.sum()),'top_angle_groups':int(len(np.unique(cg[top]))),'top_planarity_p95_mm':float(np.percentile(top_residual,95)),'outline_fit':fit,'display_fit_validation':{'passed':display_ok,'heldout_samples':int(evaluation.sum()),'heldout_angle_groups':np.unique(cg[heldout]).tolist(),'height_error_p95_mm':float(np.percentile(err,95)),'outside_outline_p95_mm':float(np.percentile(outside,95))},'all_vertices_are_estimates':True,'all_faces_are_estimates':True,'measured_points_moved':False,'raw_points_modified':False,'new_measured_points_added':0,'holes_filled':True,'missing_side_faces_inferred':True,'bottom_assumed_not_measured':True,'metric_accuracy_accepted':False,'complete_shape_accuracy_verified':False}
 return {'vertices':vertices,'triangles':faces,'weak_faces':np.array([k!='estimated_top' for k in kinds]),'face_kinds':kinds,'cleaned':cleaned,'stats':stats}
