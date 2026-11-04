"""Offline exact output/quality-rule equivalence, including original PL records."""
from pathlib import Path
import sys,json,hashlib
import cv2,numpy as np
BUNDLE=Path(__file__).resolve().parents[1]
ROOT=BUNDLE/'results/host_equivalence';ROOT.mkdir(parents=True,exist_ok=True)
PROJECT=BUNDLE
REF=BUNDLE/'03_host_optimization/source_snapshot'
sys.path[:0]=[str(REF),str(BUNDLE/'04_runtime'),str(BUNDLE/'02_weak_candidate/reference')]
from quality_from_stripes import review_from_stripes
from quality_from_stripes_fast import review_from_stripes_fast,extract_and_review_fast,_review_frame
from laser_horizontal_check import extract_horizontal
from laser_vertical_fast import _score_crop
from weak_line_candidate import extract_weak_seeded
from host_image_fast import pack_bgr_fast,pack_bgr_into

def exact(a,b):
    assert a[1]==b[1],(a[1],b[1])
    assert np.array_equal(a[0],b[0]),'Coordinates/masks changed'

def oracle_frame(image,stripe,roi,scores=None):
    # Verbatim decisions from frozen loop, exposing individual reason flags.
    if scores is None:scores=_score_crop(image.transpose(1,0,2),(roi[1],roi[0],roi[3],roi[2]))
    keep=np.asarray(stripe['valid'],bool).copy();y=np.asarray(stripe['y'])
    columns=np.flatnonzero(keep);flags={'columns':columns,'saturation':[],'white_core':[],'multiple_peaks':[]}
    for x in columns:
        iy=round(float(y[x]));band=image[max(0,iy-10):min(480,iy+11),x]
        profile=scores[x-roi[0]];peak=int(np.argmax(profile))
        maxima=np.flatnonzero((profile[1:-1]>=profile[:-2])&(profile[1:-1]>profile[2:]))+1
        other=maxima[(abs(maxima-peak)>=4)&(abs(maxima-peak)<=10)]
        f={'saturation':bool((band[:,2]>=250).any()),'white_core':bool((band.min(axis=1)>=245).any()),'multiple_peaks':bool((profile[other]>=.7*profile[peak]).any())}
        for k,v in f.items():flags[k].append(v)
        keep[x]=not any(f.values())
    for k in ('saturation','white_core','multiple_peaks'):flags[k]=np.asarray(flags[k],bool)
    return np.where(keep,y,np.nan),int(keep.sum()),flags

checks=[];frame_checks=0;hashes={}
def verify_case(name,images,stripes,roi,*,fused=False):
    global frame_checks
    old=review_from_stripes(images,stripes,roi);new=review_from_stripes_fast(images,stripes,roi);exact(old,new)
    if fused:exact(old,extract_and_review_fast(images,roi))
    for im,s in zip(images,stripes):
        a=oracle_frame(im,s,roi);b=_review_frame(im,s,roi,diagnostics=True)
        assert a[1]==b[1] and np.array_equal(a[0],b[0],equal_nan=True)
        for k in ['columns','saturation','white_core','multiple_peaks']:assert np.array_equal(a[2][k],b[3][k]),(name,k)
        frame_checks+=1
    checks.append({'name':name,'common_points':len(old[0]),'exact_points_report_and_rejection_masks':True,'fused_path_checked':fused})

def read(path):
    hashes[str(path.relative_to(PROJECT))]=hashlib.sha256(path.read_bytes()).hexdigest()
    im=cv2.imread(str(path));assert im is not None;return im

m=json.loads((PROJECT/'02_weak_candidate/manifest.json').read_text())
packet=PROJECT/'02_weak_candidate'
for group in m['three_frame_groups']:
    images=[read(packet/p) for p in group['images']];roi=tuple(group['roi'])
    for mode,fn in [('legacy',extract_horizontal),('weak_candidate',extract_weak_seeded)]:
        stripes=[fn(im,roi,12,8) for im in images]
        verify_case(group['name']+'/'+mode,images,stripes,roi,fused=mode=='legacy')

for case in m['cases']:
    im=read(packet/case['packet_image']);roi=tuple(case['roi'])
    for mode,fn in [('legacy',extract_horizontal),('weak_candidate',extract_weak_seeded)]:
        s=fn(im,roi,case['threshold'],8)
        verify_case(case['id']+'/'+mode,[im]*3,[s]*3,roi,fused=mode=='legacy' and case['threshold']==12)

# Real PL/Q8 records, preserving their own quantized centres exactly.
for j,folder in enumerate(['on_before','blocked','on_after']):
    ev=PROJECT/'07_evidence/on_block_on/board_evidence';ims=[read(ev/folder/f'raw_{i:02}.png') for i in range(3)];ss=[]
    for i in range(3):
        p=ev/'comparison'/f'{j*3+i+1:03}_actual.bin';w=np.fromfile(p,dtype='<u4');v=w>>31!=0
        ss.append({'valid':v,'y':np.where(v,(w&0x3ffff).astype(float)/256,np.nan)})
    verify_case('actual_PL/'+folder,ims,ss,(160,215,410,270))

live=PROJECT/'07_evidence/live_validation/board_evidence'
for folder,prefix in [('capture_01','comparison_initial'),('capture_exposure20','comparison_exposure20')]:
    ims=[read(live/folder/f'raw_{i:02}.png') for i in range(3)]
    files=sorted((live/prefix).glob('[0-9]*_full.hex'))
    if len(files)!=3:raise RuntimeError((prefix,files))
    ss=[]
    for p in files:
        w=np.asarray([int(line,16) for line in p.read_text().splitlines() if line.strip()],np.uint32);v=w>>31!=0;ss.append({'valid':v,'y':np.where(v,(w&0x3ffff).astype(float)/256,np.nan)})
    verify_case('actual_PL/'+folder,ims,ss,(160,215,410,270))

# Slicing/rounding, competitor ratio/distance/plateau and threshold boundaries.
rng=np.random.default_rng(20261020)
for trial in range(20):
    im=rng.integers(0,256,(480,640,3),dtype=np.uint8)
    roi=[(0,0,640,480),(17,0,39,1),(0,478,20,480),(620,450,640,480)][trial%4]
    ss=[]
    for i in range(3):
        v=np.zeros(640,bool);v[roi[0]:roi[2]]=rng.random(roi[2]-roi[0])<.7
        yy=rng.choice(np.array([0,.5,1.5,2.5,237.5,478.5,479]),640).astype(float)
        ss.append({'valid':v,'y':np.where(v,yy,np.nan)})
    verify_case(f'random_boundary_{trial}',[im]*3,ss,roi)

profile_cases=0
for distance in [3,4,10,11,-3,-4,-10,-11]:
    for competitor in [np.nextafter(np.float32(14),np.float32(-np.inf)),np.float32(14),np.nextafter(np.float32(14),np.float32(np.inf))]:
        im=np.zeros((480,640,3),np.uint8);roi=(100,200,108,250)
        scores=np.zeros((8,50),np.float32);scores[:,25]=20;scores[:,25+distance]=competitor
        v=np.zeros(640,bool);v[100:108]=True;y=np.full(640,np.nan);y[100:108]=[0,.5,1.5,2.5,478.5,479,236.5,237.5]
        for x,row in zip(range(100,108),[10,10,12,13,469,468,246,248]):im[row,x,2]=250
        im[100,100]=[245,245,245];s={'valid':v,'y':y}
        a=oracle_frame(im,s,roi,scores);b=_review_frame(im,s,roi,scores,diagnostics=True)
        assert np.array_equal(a[0],b[0],equal_nan=True)
        for k in a[2]:assert np.array_equal(a[2][k],b[3][k]),(distance,competitor,k)
        profile_cases+=1

# Packing normal, reversed/strided sources and all original53 case images.
packing_cases=0;out=np.empty((480,640),np.uint32)
for case in m['cases']:
    im=cv2.imread(str(packet/case['packet_image']))
    for src in [im,im[::-1],im[:,::-1],im[:,:,::-1]]:
        p=src.astype(np.uint32);wanted=(p[:,:,2]<<16)|(p[:,:,1]<<8)|p[:,:,0]
        assert np.array_equal(pack_bgr_fast(src),wanted)
        out.fill(0xffffffff);assert pack_bgr_into(src,out) is out
        assert np.array_equal(out,wanted) and np.array_equal(pack_bgr_into(src,out.ravel()).reshape(480,640),wanted)
        packing_cases+=1
result={'numpy_version':np.__version__,'group_cases':len(checks),'frame_reason_mask_comparisons':frame_checks,'controlled_competitor_boundary_cases':profile_cases,'packing_layout_cases':packing_cases,'all_exact':True,'hardware_tested':False,'checks':checks}
report_name=sys.argv[sys.argv.index('--report')+1] if '--report' in sys.argv else 'equivalence.json'
(ROOT/report_name).write_text(json.dumps(result,ensure_ascii=False,indent=2));(ROOT/'source_hashes.json').write_text(json.dumps(hashes,ensure_ascii=False,indent=2))
print(json.dumps({k:v for k,v in result.items() if k!='checks'},indent=2))
