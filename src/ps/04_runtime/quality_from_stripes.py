"""Apply the frozen PS quality rules to supplied PS or PL stripe coordinates."""
import numpy as np
from laser_vertical_fast import _score_crop

def review_from_stripes(images, stripes, roi, minimum_common_columns=20):
    if len(images) != 3 or len(stripes) != 3 or minimum_common_columns < 20:
        raise ValueError('Require three frames, three stripes and at least 20 columns')
    ys=[]; counts=[]; rejections=[]
    for image,stripe in zip(images,stripes):
        if image is None or image.shape != (480,640,3):
            raise ValueError('Need original 640x480 BGR frames')
        scores=_score_crop(image.transpose(1,0,2),(roi[1],roi[0],roi[3],roi[2]))
        keep=np.asarray(stripe['valid'],dtype=bool).copy()
        y_coordinates=np.asarray(stripe['y'])
        if keep.shape != (640,) or y_coordinates.shape != (640,):
            raise ValueError('Need 640 supplied column records')
        if keep[:roi[0]].any() or keep[roi[2]:].any():
            raise ValueError('Supplied valid column outside selected ROI')
        bad={'saturation':0,'white_core':0,'multiple_peaks':0}
        for x in np.flatnonzero(keep):
            y=float(y_coordinates[x]); iy=round(y)
            band=image[max(0,iy-10):min(480,iy+11),x]
            profile=scores[x-roi[0]]; peak=int(np.argmax(profile))
            maxima=np.flatnonzero((profile[1:-1]>=profile[:-2])&(profile[1:-1]>profile[2:]))+1
            other=maxima[(abs(maxima-peak)>=4)&(abs(maxima-peak)<=10)]
            flags={'saturation':bool((band[:,2]>=250).any()),
                   'white_core':bool((band.min(axis=1)>=245).any()),
                   'multiple_peaks':bool((profile[other]>=.7*profile[peak]).any())}
            for key,value in flags.items(): bad[key]+=int(value)
            keep[x]=not any(flags.values())
        ys.append(np.where(keep,y_coordinates,np.nan))
        counts.append(int(keep.sum())); rejections.append(bad)
    ys=np.asarray(ys)
    valid=np.isfinite(ys).all(axis=0)
    valid[valid]&=np.ptp(ys[:,valid],axis=0)<=1.5
    x=np.flatnonzero(valid)
    pixels=np.column_stack([x,np.median(ys[:,x],axis=0)]) if len(x) else np.empty((0,2))
    report={'roi':list(roi),'threshold':12,'min_run':8,
            'max_three_frame_range_px':1.5,'minimum_common_columns':minimum_common_columns,
            'quality_columns_per_frame':counts,'rejection_counts':rejections,
            'common_quality_columns':len(x),
            'valid_fraction_of_selected_roi':len(x)/(roi[2]-roi[0]),
            'image_quality_gate_passed':bool(len(x)>=minimum_common_columns),
            'geometric_or_height_filter_applied':False}
    return pixels,report
