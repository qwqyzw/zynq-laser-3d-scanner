"""Upright primitive estimates from measured points; never changes raw XYZ."""
import numpy as np
from scipy.spatial import ConvexHull,cKDTree
from scipy.optimize import least_squares
LABELS={'cone':'圆锥','hex_prism':'六棱柱','cube':'正方体'}
def clean(points):
 d,_=cKDTree(points).query(points,k=min(9,len(points)));q=d[:,1:].mean(axis=1);m=np.median(q);mad=np.median(abs(q-m));keep=q<=m+4*max(1.4826*mad,m*.05,1e-6)
 if keep.mean()<.9:keep[:]=True
 return points[keep],keep
def prism(poly,base,top):
 n=len(poly);v=np.vstack([np.c_[poly,np.full(n,base)],np.c_[poly,np.full(n,top)]]);f=[]
 for i in range(1,n-1):f.append([0,i+1,i]);f.append([n,n+i,n+i+1])
 for i in range(n):j=(i+1)%n;f.extend([[i,j,j+n],[i,j+n,i+n]])
 return v,np.asarray(f,int)
def cube_fit(p):
 xy=p[:,:2];angles=np.linspace(0,np.pi/2,181,endpoint=False);options=[]
 for t in angles:
  rot=np.array([[np.cos(t),-np.sin(t)],[np.sin(t),np.cos(t)]])
  q=xy@rot;lo,hi=np.percentile(q,[.5,99.5],axis=0);options.append((np.prod(hi-lo),t,rot,lo,hi))
 _,theta,rot,lo,hi=min(options,key=lambda a:a[0]);centre=((lo+hi)/2)@rot.T;edge=float(max(max(hi-lo),np.percentile(p[:,2],99)-np.percentile(p[:,2],1)));ztop=float(np.percentile(p[:,2],99))
 if edge<.5:raise ValueError('Measured cube footprint is too small')
 def residual(v,q):
  cx,cy,cz,a,t=v;rr=np.array([[np.cos(t),-np.sin(t)],[np.sin(t),np.cos(t)]]);local=np.c_[(q[:,:2]-[cx,cy])@rr,q[:,2]-cz]
  delta=abs(local)-a/2
  return np.linalg.norm(np.maximum(delta,0),axis=1)+np.minimum(np.max(delta,axis=1),0)
 v0=[*centre,ztop-edge/2,edge,theta]
 def fitting(v):
  r=residual(v,p)
  return np.where(r>0,r*4,r)
 opt=least_squares(fitting,v0,loss='soft_l1',f_scale=.6,max_nfev=150,bounds=([centre[0]-edge*.3,centre[1]-edge*.3,ztop-edge*.8,edge*.9,theta-.35],[centre[0]+edge*.3,centre[1]+edge*.3,ztop-edge*.2,edge*1.4,theta+.35]))
 cx,cy,cz,a,t=opt.x;rr=np.array([[np.cos(t),-np.sin(t)],[np.sin(t),np.cos(t)]]);poly=np.array([[-1,-1],[1,-1],[1,1],[-1,1]])*a/2@rr.T+[cx,cy]
 v,f=prism(poly,cz-a/2,cz+a/2)
 return v,f,abs(residual(opt.x,p)),{'edge_estimated_mm':float(a),'bottom_estimated_z_mm':float(cz-a/2),'top_estimated_z_mm':float(cz+a/2),'dimensions_note':'估计边长 %.2f mm'%a,'assumption':'立放正方体先验；长宽高相等，底面位置由拟合估计。'}
def cone_fit(p):
 xy=p[:,:2];centre=np.median(xy,axis=0);z=p[:,2];base=float(np.percentile(z,1));span=max(float(np.ptp(z)),2.);r=np.linalg.norm(xy-centre,axis=1)
 slope,inter=np.polyfit(z,r,1);k=float(np.clip(-slope,.08,5.));apex=float(np.clip(inter/k,np.percentile(z,99),max(z)+span*2))
 def residual(v,q):
  cx,cy,ap,k=v;return (np.linalg.norm(q[:,:2]-[cx,cy],axis=1)-k*(ap-q[:,2]))/np.sqrt(1+k*k)
 width=max(float(np.ptp(xy,axis=0).max()),2.)
 opt=least_squares(lambda v:residual(v,p),[*centre,apex,k],loss='soft_l1',f_scale=.6,max_nfev=180,bounds=([centre[0]-width,centre[1]-width,max(z)-.1,.02],[centre[0]+width,centre[1]+width,max(z)+span*3,10.]))
 cx,cy,ap,k=opt.x;radius=float(k*(ap-base));height=float(ap-base)
 if height<.5 or radius<.5 or radius>width*3:raise ValueError('Measured points do not constrain a usable cone')
 n=64;t=np.arange(n)*2*np.pi/n;v=np.vstack([np.c_[cx+radius*np.cos(t),cy+radius*np.sin(t),np.full(n,base)],[cx,cy,ap],[cx,cy,base]])
 f=[]
 for i in range(n):j=(i+1)%n;f.extend([[i,j,n],[j,i,n+1]])
 return v,np.asarray(f,int),abs(residual(opt.x,p)),{'radius_estimated_mm':radius,'height_estimated_mm':height,'bottom_estimated_z_mm':base,'top_estimated_z_mm':float(ap),'dimensions_note':'估计底径 %.2f mm · 高 %.2f mm'%(radius*2,height),'assumption':'立放正圆锥先验；圆锥侧面拟合测点，底面取可见测点低端高度估计。'}
def hex_fit(p,g):
 # Reuse the verified flat-top detector, but keep all new outputs in89.
 from hex_model import top_band,footprint,edge_distances
 mask,height=top_band(p,g);poly,fit=footprint(p[mask],g[mask]);base=0.
 v,f=prism(poly,base,height)
 side=edge_distances(p[:,:2],poly).min(axis=1);outside=[]
 for a,b in zip(poly,np.roll(poly,-1,axis=0)):
  e=b-a;n=np.array([e[1],-e[0]])/np.linalg.norm(e);outside.append(p[:,:2]@n-n@a)
 out=np.maximum(np.max(outside,axis=0),0);res=np.minimum.reduce([side,abs(p[:,2]-height),abs(p[:,2]-base)])+out
 return v,f,res,{'height_estimated_mm':float(height),'top_estimated_z_mm':float(height),'bottom_estimated_z_mm':base,'outline_fit':fit,'dimensions_note':'估计高度 %.2f mm · 六个竖直侧面'%height,'assumption':'立放凸六棱柱先验；顶面与轮廓由测点拟合，侧面竖直、底面采用台面参考高度。'}
def estimate(points,groups,shape):
 p=np.asarray(points,float);g=np.asarray(groups,int);original=p.copy()
 if shape not in LABELS or p.ndim!=2 or p.shape[1]!=3 or len(p)<30 or len(p)>50000 or g.shape!=(len(p),) or not np.isfinite(p).all():raise ValueError('Need30..50000 finite measured points and a supported explicit shape')
 q,keep=clean(p);cg=g[keep]
 if len(np.unique(cg))<3:raise ValueError('Need at least three measured angles')
 if shape=='cube':v,f,res,params=cube_fit(q)
 elif shape=='cone':v,f,res,params=cone_fit(q)
 else:v,f,res,params=hex_fit(q,cg)
 edges=np.sort(np.concatenate([f[:,[0,1]],f[:,[1,2]],f[:,[2,0]]]),axis=1);_,counts=np.unique(edges,axis=0,return_counts=True)
 if not np.isfinite(v).all() or not (counts==2).all():raise ValueError('Estimated display mesh is not finite and closed')
 centre=v.mean(axis=0);vol=float(np.sum(np.einsum('ij,ij->i',v[f[:,0]],np.cross(v[f[:,1]],v[f[:,2]])))/6)
 if vol<0:f=f[:,[0,2,1]]
 p95=float(np.percentile(res,95));warnings=[]
 if p95>3:warnings.append('测点与所选几何形状的拟合偏差较大；仅供外形预览，请核对物体类型。')
 if len(np.unique(cg))<30:warnings.append('部分角度测点缺失，模型包含按指定形状估计的区域。')
 assert np.array_equal(original,p)
 stats={'raw_points':len(p),'foreground_points':len(p),'denoised_points':len(q),'mesh_vertices':len(v),'mesh_faces':len(f),'boundary_edges':0,'watertight':True,'closed_for_display_only':True,'shape_type':shape,'shape_label':LABELS[shape],'shape_prior_explicitly_selected':True,'fit_residual_median_mm':float(np.median(res)),'fit_residual_p95_mm':p95,'display_fit_check_passed':p95<=3,'raw_points_modified':False,'measured_points_moved':False,'new_measured_points_added':0,'all_faces_are_estimates':True,'metric_accuracy_accepted':False,'complete_shape_accuracy_verified':False,'warnings':warnings,**params}
 return {'vertices':v,'triangles':f,'cleaned':q,'weak_faces':np.ones(len(f),int),'stats':stats}
