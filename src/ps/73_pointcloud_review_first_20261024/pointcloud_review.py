"""Review existing Zynq XYZ before surface inference; no new XYZ or fitting."""
from pathlib import Path
import csv,json,hashlib,sys
import numpy as np

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')

def assess(rows,angles):
    if len(angles)<3 or angles[0]!=0 or angles[-1]!=360 or len(set(angles))!=len(angles):
        raise ValueError('Explicit complete nominal angle plan required')
    counts=[0]*len(angles);xyz=[];heights=[]
    for row in rows:
        index=float(row['point_index'])
        if not index.is_integer() or not 0<=index<len(angles):raise ValueError('Point index outside plan')
        p=[float(row[k]) for k in ['x_mm','y_mm','z_mm']];height=float(row['height_above_reference_mm'])
        if not np.isfinite(p+[height]).all():raise ValueError('Nonfinite measured point')
        counts[int(index)]+=1;xyz.append(p);heights.append(height)
    xyz=np.asarray(xyz,float).reshape(-1,3);heights=np.asarray(heights,float)
    unique_counts=counts[:-1];empty=[angles[i] for i,c in enumerate(unique_counts) if c==0]
    low=[angles[i] for i,c in enumerate(unique_counts) if 0<c<10]
    consecutive=best=0
    for count in unique_counts*2:
        consecutive=consecutive+1 if count==0 else 0;best=max(best,consecutive)
    best=min(best,len(unique_counts));step=float(angles[1]-angles[0])
    result={'coordinate_producer':'zynq','coordinate_computation_performed':False,'surface_fitted':False,
        'points':len(rows),'pose_count_including_closure':len(angles),'unique_angular_samples':len(unique_counts),
        'per_pose':[{'angle_deg':a,'object_points':c,'diagnostic_low_object_support':c<10} for a,c in zip(angles,counts)],
        'empty_unique_angles_deg':empty,'low_object_support_angles_deg':low,
        'nonempty_unique_angle_fraction':sum(c>0 for c in unique_counts)/len(unique_counts),
        'maximum_empty_arc_deg':best*step,'minimum_object_points_per_unique_pose':min(unique_counts),
        'diagnostic_object_support_reference':10,'image_quality_threshold_changed':False,
        'surface_construction':'deferred_pending_pointcloud_and_shape_review',
        'metric_accuracy_accepted':False,'true_object_height_mm':None,
        'notes':['Nonempty profile does not prove full surface coverage.',
                 'Minimum10 here is a diagnostic count after object filtering; acquisition common10 is unchanged.',
                 'Reference height>=3mm filtering omits the lowest object region; do not infer its underside.']}
    if len(rows):
        q=np.percentile(heights,[0,5,50,95,100])
        result['measured_height_mm']=dict(zip(['min','p05','median','p95','max'],map(float,q)))
        result['height_p95_minus_p05_mm']=float(q[3]-q[1]);result['bounds_mm']=[xyz.min(axis=0).tolist(),xyz.max(axis=0).tolist()]
        sv=np.linalg.svd(xyz-xyz.mean(axis=0),compute_uv=False) if len(rows)>=3 else np.zeros(3)
        result['singular_values']=sv.tolist();result['near_planar_coordinate_distribution']=bool(sv[-1]/max(sv[0],1e-12)<.02)
        result['measured_height_bin_counts']=[{'range_mm':[a,b],'points':int(((heights>=a)&(heights<b)).sum())} for a,b in [(3,5),(5,10),(10,15),(15,20),(20,30),(30,60)]]
    return result,xyz,heights

def render_diagnostics(path,xyz,heights,result):
    from PIL import Image,ImageDraw
    im=Image.new('RGB',(1400,1080),(246,248,251));d=ImageDraw.Draw(im)
    d.text((32,18),'NEW MEASURED POINTCLOUD REVIEW - NO SURFACE FITTING',fill=(25,40,60))
    if not len(xyz):d.text((40,100),'NO VALID OBJECT POINTS',fill=(190,30,30));im.save(path);return
    panels=[(30,60,675,500,0,1,'TOP: X / Y'),(725,60,1370,500,0,2,'FRONT: X / Z'),(30,565,675,1005,1,2,'SIDE: Y / Z')]
    for left,top,right,bottom,ix,iy,title in panels:
        d.rectangle((left,top,right,bottom),outline=(180,190,205),fill=(22,38,54));d.text((left+12,top+10),title+' [mm]',fill=(220,232,242))
        lo=xyz[:,[ix,iy]].min(axis=0);hi=xyz[:,[ix,iy]].max(axis=0);scale=min((right-left-70)/max(hi[0]-lo[0],1),(bottom-top-70)/max(hi[1]-lo[1],1));centre=(lo+hi)/2
        for p,h in zip(xyz,heights):
            x=(left+right)/2+(p[ix]-centre[0])*scale;y=(top+bottom)/2-(p[iy]-centre[1])*scale
            t=float((h-heights.min())/max(np.ptp(heights),1e-6));colour=(int(60+195*t),int(210-110*t),int(255-190*t))
            d.ellipse((x-2,y-2,x+2,y+2),fill=colour)
        d.text((left+12,bottom-23),'span %.2f x %.2f mm'%tuple(hi-lo),fill=(220,232,242))
    left,top,right,bottom=725,565,1370,1005;d.rectangle((left,top,right,bottom),outline=(180,190,205),fill=(255,255,255))
    d.text((left+12,top+12),'OBJECT POINTS BY NOMINAL ANGLE',fill=(25,40,60));counts=[p['object_points'] for p in result['per_pose'][:-1]];maximum=max(max(counts),1)
    width=(right-left-40)/len(counts)
    for i,c in enumerate(counts):
        x=left+20+i*width;y=bottom-40-c/maximum*(bottom-top-100)
        d.rectangle((x,y,x+max(width-2,1),bottom-40),fill=(55,132,189) if c>=10 else (229,139,55))
        if c==0:d.ellipse((x,bottom-44,x+5,bottom-39),fill=(205,40,40))
    d.text((left+20,bottom-25),'0 deg                      180 deg                      350 deg',fill=(50,60,70))
    d.text((32,1040),'%d points | empty angles: %s | reconstructed heights only; true height unknown'%(result['points'],str(result['empty_unique_angles_deg'])),fill=(30,50,70));im.save(path)

def review(root,output=None):
    P=Path(__file__).resolve().parent.parent;sys.path.insert(0,str(P/'09_tools'))
    from board_pointcloud_export import board_identity
    if not board_identity()['is_linux_arm_zynq_or_pynq']:raise ValueError('Real pointcloud review runs on Zynq')
    root=Path(root).resolve();source=root/'model/object_points.csv';before=sha(source)
    metadata=json.loads((root/'model/object_metadata.json').read_text())
    export=json.loads((root/'model/viewer/export_manifest.json').read_text())
    if metadata.get('data_origin')!='hardware_scan' or metadata.get('coordinate_producer')!='zynq':
        raise ValueError('Existing actual Zynq pointcloud metadata required')
    if export['outputs']['points.csv']['sha256']!=before:
        raise ValueError('Raw XYZ and board export do not match')
    trial=root/'trial.json'
    if trial.exists():
        scan=json.loads(trial.read_text());angles=scan['plan']['angles_deg'];closure=scan.get('closure');kind='paired10degree_scan'
    else:
        scan=json.loads((root/'scan/completion.json').read_text());angles=scan['plan']['acquisition_angles_deg']
        meta=json.loads((root/'model/object_metadata.json').read_text());closure=meta.get('fullturn_closure');kind='frozen61_5degree_scan'
    with source.open(newline='',encoding='utf-8-sig') as f:rows=list(csv.DictReader(f))
    result,xyz,heights=assess(rows,angles)
    result.update(run_id=root.name,source_csv_sha256=before,scan_kind=kind,endpoint_closure=closure,
                  operator_shape_not_used_for_fitting=True)
    output=Path(output) if output is not None else root/'model/pointcloud_review';output.mkdir(parents=True,exist_ok=False)
    save(output/'review.json',result);render_diagnostics(output/'pointcloud_diagnostics.png',xyz,heights,result)
    with (output/'angle_counts.csv').open('x',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['angle_deg','object_points','diagnostic_low_object_support']);writer.writeheader();writer.writerows(result['per_pose'])
    if sha(source)!=before:raise ValueError('Measured input changed during review')
    save(output/'review_manifest.json',{'source_csv_sha256':before,'surface_fitted':False,'coordinate_producer':'zynq',
        'files':{p.name:{'sha256':sha(p),'bytes':p.stat().st_size} for p in output.iterdir() if p.is_file()}})
    return result

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--run',required=True,type=Path);args=parser.parse_args()
    print(json.dumps(review(args.run),ensure_ascii=False))
