"""Additional directed image cases; expected comes from the frozen integer oracle."""
from pathlib import Path
import json,sys
import numpy as np
from project_paths import ROOT, baseline_root
sys.path.insert(0,str(baseline_root()/'reference'))
from integer_model import extract_integer
out=ROOT/'results/extra'
for d in ['vectors','expected','actual']:(out/d).mkdir(parents=True,exist_ok=True)
cases=[]
def add(name,image,roi,threshold=3072):
    words=image[:,:,0].astype(np.uint32)|(image[:,:,1].astype(np.uint32)<<8)|(image[:,:,2].astype(np.uint32)<<16)
    np.savetxt(out/'vectors'/f'{name}_input.hex',words.ravel(),fmt='%08x')
    for mode,mr in [('core',0),('full',8)]:
        golden=extract_integer(image,roi,threshold,mr)
        np.savetxt(out/'expected'/f'{name}_{mode}.hex',golden,fmt='%08x')
        cases.append({'name':name,'mode':mode,'roi':roi,'threshold_q8':threshold,'min_run':mr})
def blank():return np.full((480,640,3),20,np.uint8)
a=blank();a[100:105,:,2]=200;add('full_roi_symmetric',a,[0,0,640,480])
a=blank();a[1:5,:,2]=200;add('top_image_edge',a,[0,0,640,20])
a=blank();a[475:479,:,2]=200;add('bottom_last_columns',a,[637,460,640,480])
a=blank();a[100:105,:,2]=200;a[200:205,:,2]=200;add('equal_peaks_smallest_y',a,[0,0,12,480])
a=blank();a[100:105,100:107,2]=200;a[100:105,120:128,2]=200;add('runs_7_and_8',a,[90,80,140,130])
a=blank();a[100:105,:,2]=200;add('roi_peak_on_first_row',a,[0,102,12,120]);add('roi_peak_on_last_row',a,[0,80,12,103])
a=np.zeros((480,640,3),np.uint8);a[:,:,0]=255;a[:,:,1]=255;add('negative_scores',a,[0,0,12,480],0)
a=blank();a[100:105,:,2]=200;add('one_column_roi',a,[639,0,640,480]);add('one_row_roi',a,[0,102,640,103])
(out/'manifest.json').write_text(json.dumps(cases,indent=2),encoding='utf-8')
(ROOT/'tb/extra_cases.txt').write_text('\n'.join(' '.join(map(str,[c['name'],c['mode'],*c['roi'],c['threshold_q8'],c['min_run']])) for c in cases)+'\n',encoding='ascii')
print('EXTRA_CASES_GENERATED',len(cases))
