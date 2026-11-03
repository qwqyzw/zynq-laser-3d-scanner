"""Version51: user-requested minimum10 common columns; all per-point checks unchanged.

The public supplied-stripe path still computes quality from each original frame.
The fused PS path reuses score_roi from that SAME extraction only, never a cache
from an earlier image. Returned reports preserve the frozen field semantics.
"""
import numpy as np
from laser_vertical_fast import _score_crop, extract_vertical

def _review_frame(image,stripe,roi,scores=None,*,diagnostics=False):
    if image is None or image.shape != (480,640,3):
        raise ValueError('Need original 640x480 BGR frames')
    keep=np.asarray(stripe['valid'],dtype=bool).copy()
    coordinates=np.asarray(stripe['y'])
    if keep.shape!=(640,) or coordinates.shape!=(640,):
        raise ValueError('Need 640 supplied column records')
    if keep[:roi[0]].any() or keep[roi[2]:].any():
        raise ValueError('Supplied valid column outside selected ROI')
    columns=np.flatnonzero(keep)
    if not len(columns):
        result=(np.full(640,np.nan),0,{'saturation':0,'white_core':0,'multiple_peaks':0})
        return result+({'columns':columns,'saturation':np.zeros(0,bool),'white_core':np.zeros(0,bool),'multiple_peaks':np.zeros(0,bool)},) if diagnostics else result
    ys=coordinates[columns].astype(np.float64)
    if not np.isfinite(ys).all() or (ys<0).any() or (ys>=480).any():
        raise ValueError('Valid stripe coordinates must be finite and inside image')
    # Python round(float(y)) is ties-to-even, including float32/Q8 inputs.
    centre_rows=np.rint(ys).astype(np.intp)
    rows=np.clip(centre_rows[None,:]+np.arange(-10,11)[:,None],0,479)
    band=image[rows,columns[None,:]]
    saturation=(band[:,:,2]>=250).any(axis=0)
    white=(band.min(axis=2)>=245).any(axis=0)
    if scores is None:
        scores=_score_crop(image.transpose(1,0,2),(roi[1],roi[0],roi[3],roi[2]))
    profile=scores[columns-roi[0]]
    peaks=np.argmax(profile,axis=1)
    peak_values=profile[np.arange(len(columns)),peaks]
    offsets=np.r_[np.arange(-10,-3),np.arange(4,11)]
    other=peaks[:,None]+offsets[None,:]
    interior=(other>=1)&(other<profile.shape[1]-1)
    clipped=np.clip(other,0,profile.shape[1]-1)
    r=np.arange(len(columns))[:,None]
    values=profile[r,clipped]
    maxima=interior&(values>=profile[r,np.maximum(clipped-1,0)])&(values>profile[r,np.minimum(clipped+1,profile.shape[1]-1)])
    # NumPy1 scalar arithmetic promotes np.float32*.7 to double, while its
    # array comparison uses the representable float32 scalar; NumPy2 keeps
    # the product float32. Match the active reference, including one-ULP ties.
    if np.asarray(.7*np.float32(1)).dtype==np.dtype(np.float64):
        limit=(peak_values.astype(np.float64)*.7).astype(profile.dtype)
    else:
        limit=(peak_values*.7).astype(profile.dtype,copy=False)
    multiple=(maxima&(values>=limit[:,None])).any(axis=1)
    rejected=saturation|white|multiple
    keep[columns]=~rejected
    result=(np.where(keep,coordinates,np.nan),int(keep.sum()),{
        'saturation':int(saturation.sum()),'white_core':int(white.sum()),
        'multiple_peaks':int(multiple.sum())})
    return result+({'columns':columns,'saturation':saturation,'white_core':white,'multiple_peaks':multiple},) if diagnostics else result

def _finish(ys,counts,rejections,roi,minimum_common_columns):
    ys=np.asarray(ys);valid=np.isfinite(ys).all(axis=0)
    valid[valid]&=np.ptp(ys[:,valid],axis=0)<=1.5
    x=np.flatnonzero(valid)
    pixels=np.column_stack([x,np.median(ys[:,x],axis=0)]) if len(x) else np.empty((0,2))
    report={'roi':list(roi),'threshold':12,'min_run':8,
            'max_three_frame_range_px':1.5,'minimum_common_columns':minimum_common_columns,
            'quality_columns_per_frame':counts,'rejection_counts':rejections,
            'common_quality_columns':len(x),'valid_fraction_of_selected_roi':len(x)/(roi[2]-roi[0]),
            'image_quality_gate_passed':bool(len(x)>=minimum_common_columns),
            'geometric_or_height_filter_applied':False}
    return pixels,report

def review_from_stripes_fast(images,stripes,roi,minimum_common_columns=10):
    if len(images)!=3 or len(stripes)!=3 or minimum_common_columns<10:
        raise ValueError('Require three frames, three stripes and at least 10 columns')
    ys=[];counts=[];rejections=[]
    for image,stripe in zip(images,stripes):
        y,n,flags=_review_frame(image,stripe,roi)
        ys.append(y);counts.append(n);rejections.append(flags)
    return _finish(ys,counts,rejections,roi,minimum_common_columns)

def extract_and_review_fast(images,roi,minimum_common_columns=10):
    """Fixed threshold12/min_run8 PS path, reusing only same-frame scores."""
    if len(images)!=3 or minimum_common_columns<10:
        raise ValueError('Require three frames and at least 10 columns')
    ys=[];counts=[];rejections=[]
    for image in images:
        if image is None or image.shape!=(480,640,3):
            raise ValueError('Need original 640x480 BGR frames')
        v=extract_vertical(image.transpose(1,0,2),roi=(roi[1],roi[0],roi[3],roi[2]),threshold=12,min_run=8)
        stripe={'valid':v['valid'],'y':v['x']}
        y,n,flags=_review_frame(image,stripe,roi,v['score_roi'])
        ys.append(y);counts.append(n);rejections.append(flags)
    return _finish(ys,counts,rejections,roi,minimum_common_columns)

def common_stripe_pixels_fast(paths,roi,minimum_common_columns=10):
    """File-wrapper counterpart; image loading is not counted as compute speed."""
    if len(paths)!=3:raise ValueError('Require three original frames')
    import cv2
    return extract_and_review_fast([cv2.imread(str(p)) for p in paths],roi,minimum_common_columns)
