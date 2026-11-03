"""Frozen image-only scan gate. No hardware access or height/reference filtering."""
from pathlib import Path
import cv2
import numpy as np
from laser_horizontal_check import extract_horizontal
from laser_vertical_fast import _score_crop


def common_stripe_pixels(paths, roi, minimum_common_columns=20):
    if len(paths) != 3 or minimum_common_columns < 20:
        raise ValueError('Require three original frames and at least20 common columns')
    ys=[];counts=[];rejections=[]
    for path in paths:
        image=cv2.imread(str(Path(path)))
        if image is None or image.shape != (480,640,3):
            raise ValueError('Need the calibrated640x480 image mode')
        stripe=extract_horizontal(image,roi,threshold=12,min_run=8)
        scores=_score_crop(image.transpose(1,0,2),(roi[1],roi[0],roi[3],roi[2]))
        keep=stripe['valid'].copy();bad={'saturation':0,'white_core':0,'multiple_peaks':0}
        for x in np.flatnonzero(keep):
            y=float(stripe['y'][x]);iy=round(y);band=image[max(0,iy-10):min(480,iy+11),x]
            profile=scores[x-roi[0]];peak=int(np.argmax(profile))
            maxima=np.flatnonzero((profile[1:-1]>=profile[:-2])&(profile[1:-1]>profile[2:]))+1
            other=maxima[(abs(maxima-peak)>=4)&(abs(maxima-peak)<=10)]
            flags={'saturation':bool((band[:,2]>=250).any()),'white_core':bool((band.min(axis=1)>=245).any()),'multiple_peaks':bool((profile[other]>=.7*profile[peak]).any())}
            for key,value in flags.items():bad[key]+=int(value)
            keep[x]=not any(flags.values())
        ys.append(np.where(keep,stripe['y'],np.nan));counts.append(int(keep.sum()));rejections.append(bad)
    ys=np.asarray(ys);valid=np.isfinite(ys).all(axis=0)
    valid[valid]&=np.ptp(ys[:,valid],axis=0)<=1.5
    x=np.flatnonzero(valid)
    pixels=np.column_stack([x,np.median(ys[:,x],axis=0)]) if len(x) else np.empty((0,2))
    report={'roi':list(roi),'threshold':12,'min_run':8,'max_three_frame_range_px':1.5,'minimum_common_columns':minimum_common_columns,'quality_columns_per_frame':counts,'rejection_counts':rejections,'common_quality_columns':len(x),'valid_fraction_of_selected_roi':len(x)/(roi[2]-roi[0]),'image_quality_gate_passed':bool(len(x)>=minimum_common_columns),'geometric_or_height_filter_applied':False}
    return pixels,report
