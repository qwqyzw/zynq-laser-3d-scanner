"""Keep frozen frame checks/common10 gate; recover only stable two-frame chains.

No XYZ, interpolation or exposure change here. Newly retained y is one actual
Q8 measurement, never an average of two disagreeing locations.
"""
import numpy as np
from quality_columns10 import _review_frame, _finish

POLICY={'minimum_frame_support':2,'maximum_supported_range_px':1.5,
        'minimum_same_pair_chain':3,'maximum_neighbour_y_jump_px':6.,
        'three_observed_conflict_policy':'reject','single_frame_policy':'reject'}

def combine_reviewed(ys,counts,rejections,roi):
    ys=np.asarray(ys,float)
    if ys.shape!=(3,640) or np.isinf(ys).any():raise ValueError('Three finite-or-missing 640-column reviews required')
    if not (len(roi)==4 and 0<=roi[0]<roi[2]<=640 and 0<=roi[1]<roi[3]<=480):raise ValueError('Invalid ROI')
    finite=np.isfinite(ys)
    if finite[:,:roi[0]].any() or finite[:,roi[2]:].any() or np.any((ys[finite]<0)|(ys[finite]>=480)):
        raise ValueError('Reviewed measurements outside image/ROI')
    baseline,report=_finish(ys,counts,rejections,roi,10)
    common={int(x):float(y) for x,y in baseline};candidates=[]
    for x in range(roi[0],roi[2]):
        indices=np.flatnonzero(finite[:,x])
        if len(indices)!=2:continue  # Three-frame conflicts cannot become cherry-picked pairs.
        values=ys[indices,x]
        if np.ptp(values)>1.5:continue
        candidates.append({'x':x,'y':float(values[0]),'frames':indices.tolist(),
                           'source_frame':int(indices[0]),'range_px':float(np.ptp(values))})
    supported=[];start=0
    for i in range(1,len(candidates)+1):
        split=i==len(candidates) or candidates[i]['x']!=candidates[i-1]['x']+1 or candidates[i]['frames']!=candidates[i-1]['frames'] or abs(candidates[i]['y']-candidates[i-1]['y'])>6.
        if split:
            if i-start>=3:supported.extend(candidates[start:i])
            start=i
    # The user's common10 image gate remains based on THREE frames. Two-frame
    # additions cannot convert a failed image batch into an accepted batch.
    additions=supported if report['image_quality_gate_passed'] else []
    merged=dict(common);merged.update({r['x']:r['y'] for r in additions})
    pixels=np.array(sorted(merged.items()),float).reshape(-1,2)
    report.update(temporal_consensus_policy=POLICY,strict_common_columns=len(baseline),
                  accepted_measured_columns=len(pixels),added_two_frame_columns=len(additions),
                  two_frame_support=additions,interpolated_points=0,
                  original_common10_gate_preserved=True)
    return pixels,report

def review_from_stripes_fast(images,stripes,roi):
    if len(images)!=3 or len(stripes)!=3:raise ValueError('Three original frames and actual PL records required')
    ys=[];counts=[];reject=[]
    red_rejections=[]
    for image,stripe in zip(images,stripes):
        filtered=dict(stripe);keep=np.asarray(stripe['valid'],bool).copy();xs=np.flatnonzero(keep)
        if len(xs):
            centres=np.rint(np.asarray(stripe['y'])[xs]).astype(int)
            rows=np.clip(centres[None,:]+np.arange(-2,3)[:,None],0,479)
            band=image[rows,xs[None,:]].astype(np.int16)
            red=(band[:,:,2]>=40)&(band[:,:,2]-np.maximum(band[:,:,0],band[:,:,1])>=12)
            admitted=red.any(axis=0);keep[xs[~admitted]]=False
            red_rejections.append(int((~admitted).sum()))
        else:red_rejections.append(0)
        filtered['valid']=keep
        y,n,r=_review_frame(image,filtered,roi);ys.append(y);counts.append(n);reject.append(r)
    pixels,report=combine_reviewed(ys,counts,reject,roi)
    report['red_identity']={'absolute_R_minimum':40,'R_minus_maxBG_minimum':12,'radius_px':2,'rejected_columns_per_frame':red_rejections,'all_returned_coordinates_from_actual_PL':True}
    return pixels,report
