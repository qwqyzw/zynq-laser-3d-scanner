"""Zynq-side denoising and explicitly estimated convex surfaces.

No shape class is hard coded. Convexity is a user-visible modelling assumption.
Original samples never move. Face interiors are inferred, not measured points.
"""
from pathlib import Path
import argparse,csv,hashlib,json,sys,time
import numpy as np
from scipy.spatial import ConvexHull,cKDTree,QhullError

HERE=Path(__file__).resolve().parent
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,data):p.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')

def estimate(points):
    points=np.asarray(points,dtype=float)
    if points.ndim!=2 or points.shape[1]!=3 or len(points)<16 or len(points)>200000 or not np.isfinite(points).all():
        raise ValueError('Need16..200000 finite XYZ points')
    unique,first=np.unique(points,axis=0,return_index=True)
    if len(unique)<16:raise ValueError('Insufficient unique samples')
    dist,_=cKDTree(unique).query(unique,k=min(13,len(unique)))
    score=dist[:,1:].mean(axis=1);median=float(np.median(score));mad=float(np.median(abs(score-median)))
    limit=median+4*max(1.4826*mad,median*.05,1e-6)
    keep=score<=limit
    # Refuse aggressive filtering rather than silently deleting real corners.
    if keep.sum()<16 or np.mean(keep)<.75:raise ValueError('Denoise would remove over25percent; inspect input')
    cleaned=unique[keep];indices=first[keep]
    singular=np.linalg.svd(cleaned-cleaned.mean(axis=0),compute_uv=False)
    if singular[-1]/max(singular[0],1e-12)<.02:
        raise ValueError('Data nearly planar; cannot infer a3D volume without a stronger prior')
    hull=ConvexHull(cleaned)
    faces=hull.simplices.copy()
    a,b,c=cleaned[faces[:,0]],cleaned[faces[:,1]],cleaned[faces[:,2]]
    cross=np.cross(b-a,c-a);flip=np.einsum('ij,ij->i',cross,hull.equations[:,:3])<0
    faces[flip]=faces[flip][:,[0,2,1]]
    lengths=np.linalg.norm(cross,axis=1);areas=lengths*.5
    centres=cleaned[faces].mean(axis=1)
    gap,_=cKDTree(cleaned).query(centres,k=1)
    support_limit=max(1.5,median*3)
    unsupported=gap>support_limit
    used=np.unique(faces);remap=np.full(len(cleaned),-1,int);remap[used]=np.arange(len(used))
    vertices=cleaned[used];triangles=remap[faces]
    normals=np.zeros_like(vertices)
    face_normals=np.cross(vertices[triangles[:,1]]-vertices[triangles[:,0]],vertices[triangles[:,2]]-vertices[triangles[:,0]])
    for j in range(3):np.add.at(normals,triangles[:,j],face_normals)
    normals/=np.maximum(np.linalg.norm(normals,axis=1,keepdims=True),1e-12)
    accepted=np.zeros(len(points),bool);accepted[indices]=True
    stats={'raw_points':len(points),'unique_points':len(unique),'duplicate_points':len(points)-len(unique),
       'statistical_outliers':int((~keep).sum()),'denoised_points':len(cleaned),'mesh_vertices':len(vertices),'mesh_faces':len(triangles),
       'neighbours':12,'mean_neighbour_distance_median_mm':median,'outlier_distance_limit_mm':limit,
       'raw_bounds_mm':[points.min(axis=0).tolist(),points.max(axis=0).tolist()],
       'denoised_bounds_mm':[cleaned.min(axis=0).tolist(),cleaned.max(axis=0).tolist()],
       'singular_values':singular.tolist(),'estimated_volume_mm3':float(hull.volume),
       'estimated_surface_area_mm2':float(hull.area),'weakly_supported_faces':int(unsupported.sum()),
       'weakly_supported_area_fraction':float(areas[unsupported].sum()/areas.sum()),'face_centroid_support_limit_mm':support_limit,
       'maximum_face_centroid_distance_to_measurement_mm':float(gap.max()),
       'all_faces_are_derived':True,'measured_points_moved':False,'new_measured_points_added':0,
       'assumption':'convex object; holes/concavities and unobserved underside may be filled incorrectly',
       'metric_accuracy_accepted':False,'complete_shape_accuracy_verified':False}
    return {'cleaned':cleaned,'kept_input_rows':indices,'accepted':accepted,'vertices':vertices,'triangles':triangles,
            'normals':normals,'weak_faces':unsupported,'face_gaps':gap,'stats':stats}

def write_ply(path,points,faces=None,normals=None):
    with path.open('x',encoding='ascii') as f:
        f.write('ply\nformat ascii 1.0\ncomment units mm\n')
        f.write('comment DERIVED_ESTIMATED_SURFACE_NOT_MEASURED\n' if faces is not None else 'comment original_coordinate_samples\n')
        f.write('element vertex %d\nproperty float x\nproperty float y\nproperty float z\n'%len(points))
        if normals is not None:f.write('property float nx\nproperty float ny\nproperty float nz\n')
        if faces is not None:f.write('element face %d\nproperty list uchar int vertex_indices\n'%len(faces))
        f.write('end_header\n')
        for i,p in enumerate(points):f.write(' '.join('%.9g'%v for v in (np.r_[p,normals[i]] if normals is not None else p))+'\n')
        if faces is not None:
            for row in faces:f.write('3 '+' '.join(str(int(v)) for v in row)+'\n')

def render_png(path,model):
    from PIL import Image,ImageDraw,ImageFont
    v=model['vertices'];faces=model['triangles'];centre=(v.max(axis=0)+v.min(axis=0))/2
    # Display projection only; it does not modify model geometry.
    yaw=.65;pitch=-.42
    Rz=np.array([[np.cos(yaw),-np.sin(yaw),0],[np.sin(yaw),np.cos(yaw),0],[0,0,1]])
    rx=np.array([[1,0,0],[0,np.cos(pitch),-np.sin(pitch)],[0,np.sin(pitch),np.cos(pitch)]])
    # Camera looks along Y, world Z is up.
    R=rx@Rz;p=(v-centre)@R.T;span=max(np.ptp(p[:,0]),np.ptp(p[:,2]),1);scale=560/span
    xy=np.column_stack([640+p[:,0]*scale,390-p[:,2]*scale])
    image=Image.new('RGB',(1280,850),(23,34,49));draw=ImageDraw.Draw(image)
    light=np.array([-.4,-.7,.6]);light/=np.linalg.norm(light)
    for i in np.argsort(p[faces,1].mean(axis=1))[::-1]:
        tri=p[faces[i]];n=np.cross(tri[1]-tri[0],tri[2]-tri[0]);n/=max(np.linalg.norm(n),1e-12)
        shade=int(120+115*max(0,float(n@light)))
        draw.polygon([tuple(x) for x in xy[faces[i]]],fill=(shade,shade,min(255,shade+5)),outline=(max(0,shade-12),)*3)
    draw.text((30,25),'ZYNQ DERIVED 3D SURFACE - HISTORICAL ALGORITHM PREVIEW',fill=(235,240,250))
    draw.text((30,55),'Convex surface estimation; face interiors are inferred, not scanned.',fill=(255,199,104))
    s=model['stats'];draw.text((30,780),'Raw %d  |  Denoised %d  |  Faces %d'%(s['raw_points'],s['denoised_points'],s['mesh_faces']),fill=(220,230,240))
    image.save(path)

def run(source,expected,output,source_run,archive=True):
    sys.path.insert(0,str(HERE.parent/'09_tools'))
    from board_pointcloud_export import board_identity
    identity=board_identity()
    if not identity['is_linux_arm_zynq_or_pynq']:raise ValueError('Real-data modelling must execute on Zynq')
    if sha(source)!=expected:raise ValueError('Input hash differs')
    if output.exists():raise ValueError('Output must be a new directory')
    started=time.monotonic()
    with source.open(newline='',encoding='utf-8-sig') as f:
        rows=list(csv.DictReader(f));points=np.array([[float(r[k]) for k in ['x_mm','y_mm','z_mm']] for r in rows])
    model=estimate(points);output.mkdir(parents=True)
    write_ply(output/'measured_points.ply',points);write_ply(output/'denoised_points.ply',model['cleaned'])
    write_ply(output/'ESTIMATED_CONVEX_SURFACE.ply',model['vertices'],model['triangles'],model['normals'])
    with (output/'point_decisions.csv').open('x',newline='') as f:
        w=csv.writer(f);w.writerow(['input_row','retained']);w.writerows(enumerate(model['accepted'].astype(int)))
    with (output/'surface_support.csv').open('x',newline='') as f:
        w=csv.writer(f);w.writerow(['face','centroid_gap_mm','weak_support','all_faces_derived']);w.writerows((i,float(d),int(weak),1) for i,(d,weak) in enumerate(zip(model['face_gaps'],model['weak_faces'])))
    data={'vertices':model['vertices'].tolist(),'faces':model['triangles'].tolist(),'points':model['cleaned'].tolist(),
          'weak':model['weak_faces'].astype(int).tolist(),'stats':model['stats'],'source_run':source_run,
          'historical_algorithm_preview':archive,'coordinate_producer':'zynq'}
    template=(HERE/'viewer_template.html').read_text(encoding='utf-8')
    (output/'model_viewer.html').write_text(template.replace('__MODEL_DATA__',json.dumps(data,ensure_ascii=False)),encoding='utf-8')
    render_png(output/'model_preview.png',model)
    if sha(source)!=expected:raise ValueError('Input changed during modelling')
    meta={'status':'derived_visual_model_exported','data_origin':'hardware_scan_derived_visualization','source_run_id':source_run,
          'source_input':str(source),'source_sha256':expected,'historical_algorithm_preview':archive,
          'restores_cancelled_scan_as_current':False,'latest_result_modified':False,'coordinate_producer':'zynq',
          'execution_platform':identity,'processing_seconds':time.monotonic()-started,'statistics':model['stats'],
          'algorithm':'exact duplicate removal;robust12NN statistical filter;convex hull estimated surface',
          'source_scripts':{n:sha(HERE/n) for n in ['surface_model.py','viewer_template.html']},
          'references':['https://docs.scipy.org/doc/scipy/reference/generated/scipy.spatial.ConvexHull.html',
                        'https://docs.scipy.org/doc/scipy/reference/generated/scipy.spatial.cKDTree.html']}
    save(output/'model_metadata.json',meta)
    save(output/'artifact_manifest.json',{'coordinate_producer':'zynq','derived_estimate':True,'historical_algorithm_preview':archive,
          'files':{p.name:{'sha256':sha(p),'bytes':p.stat().st_size} for p in sorted(output.iterdir()) if p.is_file()}})
    return meta

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--input',type=Path);parser.add_argument('--input-sha256')
    parser.add_argument('--output',type=Path);parser.add_argument('--source-run');parser.add_argument('--run-board',action='store_true')
    args=parser.parse_args()
    if not args.run_board:print(json.dumps({'hardware_accessed':False,'algorithm':'denoise+estimated convex surface','real_data_computation':'Zynq only'}))
    else:
        if not all([args.input,args.input_sha256,args.output,args.source_run]):parser.error('Explicit input/hash/output/run required')
        print(json.dumps(run(args.input,args.input_sha256,args.output,args.source_run),ensure_ascii=False))
