# EXPERIMENTAL static bracket, unchanged actual PL and PS checks.
"""PL coordinates -> frozen PS quality using explicitly configured exposure.

Imports never acquire camera/UART/DMA. Parent capture remains the verified,
bounded exposure worker. No weak mode is enabled by this adapter.
Exposure 5 is the current scene default; exposure 20 remains an explicit option.
"""
from pathlib import Path
import sys
_project=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(_project/'04_runtime'))
sys.path.insert(0,str(_project/'01_fpga_baseline/reference'))
import json
import cv2,numpy as np
from diagnostic_camera import PersistentCamera
from laser_stripe import StripeConfig
from laser_vertical_fast import keep_long_runs
from laser_horizontal_check import extract_horizontal
from host_image_fast import pack_bgr_fast
from temporal_consensus import review_from_stripes_fast
import roi_integer as integer_reference

def independent_integer_reference(image, roi, actual_words):
    """Exact ROI PS integer extraction, validated against222 frozen/actual frames, compared bit for bit with PL."""
    expected_path=Path(__file__).resolve().parent/'roi_integer.py'
    if Path(integer_reference.__file__).resolve()!=expected_path.resolve():
        raise RuntimeError('Integer reference imported from another project')
    expected=integer_reference.extract_integer(image,tuple(roi),3072,0)
    if not np.array_equal(expected,np.asarray(actual_words,dtype=np.uint32)):
        raise RuntimeError('Independent integer reference differs from actual PL words')
    valid=keep_long_runs(expected>>31!=0,4)
    return {'valid':valid,'y':np.where(valid,(expected&np.uint32(0x3ffff)).astype(float)/256,np.nan)}


def checked_same_image_reference(image, roi, actual_words):
    # Exact integer agreement is mandatory, including the first-maximum tie rule.
    stripe=independent_integer_reference(image,roi,actual_words)
    words=np.asarray(actual_words,dtype=np.uint32);valid=words>>31!=0
    ref=extract_horizontal(image,roi,12,0)
    validity_equal=np.array_equal(ref['valid'],valid)
    y=(words&np.uint32(0x3ffff)).astype(float)/256
    delta=np.abs(ref['y'][valid & ref['valid']]-y[valid & ref['valid']]);maximum=float(np.max(delta,initial=0))
    bad=np.flatnonzero((valid ^ ref['valid']) | (valid & ref['valid'] & (np.abs(np.nan_to_num(ref['y'])-y)>.25)))
    ties=[]
    if len(bad):
        # Only an EXACT equal integer maximum can explain a float peak change.
        # Other differences still stop acquisition; the tolerance is unchanged.
        x0,y0,x1,y1=roi
        crop=image[:,bad].astype(np.int64)
        t=2*crop[:,:,2]-crop[:,:,0]-crop[:,:,1]
        padded=np.pad(t,((15,15),(0,0)),mode='edge')
        sums=np.pad(np.cumsum(padded,axis=0,dtype=np.int64),((1,0),(0,0)))
        d=31*t-(sums[31:]-sums[:-31]);pad=np.pad(d,((1,1),(0,0)),mode='edge')
        scores=(pad[:-2]+2*d+pad[2:])[y0:y1]
        from laser_vertical_fast import _score_crop
        fs=_score_crop(image.transpose(1,0,2),(y0,x0,y1,x1))
        for j,x in enumerate(bad):
            integer_peak=int(np.argmax(scores[:,j]));float_peak=int(np.argmax(fs[x-x0]))
            if integer_peak==float_peak or scores[float_peak,j]!=scores[integer_peak,j]:
                raise RuntimeError('Same-image non-tie coordinate comparison failed')
            ties.append({'x_px':int(x),'integer_peak_y':integer_peak+y0,
                         'float_peak_y':float_peak+y0,'integer_equal_peak_score':int(scores[integer_peak,j])})
    return stripe,{'valid_equal':bool(validity_equal),'float_integer_validity_tie_columns':np.flatnonzero(valid ^ ref['valid']).tolist(),'max_pixel_difference':maximum,
                   'integer_reference_bit_exact':True,'float_equal_peak_ties':ties,
                   'float_tie_diagnostic_only':bool(ties)}


class PersistentPLCamera(PersistentCamera):
    def __init__(self,source,roi,driver,*,verify_ps=True,exposure=5,timeout_s=40):
        super().__init__(source,exposure=exposure,timeout_s=timeout_s)
        self.roi=tuple(roi);self.driver=driver;self.verify_ps=bool(verify_ps)
        self.config=StripeConfig(*roi,threshold_q8=3072,min_run=0)

    def capture(self,folder):
        batch=super().capture(folder);folder=Path(folder)
        images=[];stripes=[];transactions=[];references=[]
        try:
            for i,item in enumerate(batch['images']):
                im=cv2.imread(item['file'])
                if im is None or im.shape!=(480,640,3):raise RuntimeError('Missing original frame')
                result=self.driver.run_words(pack_bgr_fast(im),self.config)
                result.words.astype('<u4').tofile(folder/f'pl_{i:02d}_actual.bin')
                row={'frame':i,'cycles':int(result.cycles),'completed_frames':int(result.completed_frames),'raw_valid_columns':int(result.valid.sum()),'source':'supplied_PL_driver','PS_reference_computed':self.verify_ps}
                transactions.append(row)
                if self.verify_ps:
                    reference,check=checked_same_image_reference(im,self.roi,result.words)
                    row.update(check);references.append(reference)
                images.append(im);stripes.append({'valid':keep_long_runs(result.valid,4),'y':result.y})
            pixels,quality=review_from_stripes_fast(images,stripes,self.roi)
            quality['min_run']=4
            quality['PL_min_run']=0
            quality['run_filter_producer']='zynq_PS_mask_on_actual_PL_centroids'
            quality['unchanged_three_frame_common10']=True
            (folder/'stripe_quality.json').write_text(json.dumps(quality,indent=2))
            np.savetxt(folder/'stripe_pixels.csv',pixels,delimiter=',',header='x_px,y_px',comments='')
            if self.verify_ps:
                pp,pq=review_from_stripes_fast(images,references,self.roi)
                pq['min_run']=4
                pq['PL_min_run']=0
                pq['run_filter_producer']='zynq_PS_mask_on_actual_PL_centroids'
                pq['unchanged_three_frame_common10']=True
                same_x=np.array_equal(pp[:,0],pixels[:,0])
                dy=float(np.max(abs(pp[:,1]-pixels[:,1]),initial=0)) if same_x else None
                comparison={'reference_coordinate_representation':'frozen_independent_integer_Q8','valid_columns_equal':same_x,'quality_reports_equal':pq==quality,'max_y_difference_px':dy}
                (folder/'quality_comparison.json').write_text(json.dumps(comparison,indent=2))
                if not same_x or pq!=quality or dy>.25:raise RuntimeError('PS/PL quality review differs')
            batch['exposure_quality_gate_passed']=quality['image_quality_gate_passed']  # failed exposure remains false and is excluded by fusion
            batch.update(stripe_quality=quality,stripe_pixels_file=str((folder/'stripe_pixels.csv').resolve()),stripe_coordinate_source='supplied_PL_driver',PS_reference_only=self.verify_ps,adapter_hardware_acceptance_pending=True)
            return batch
        except BaseException as exc:
            self.capture_blocked=True
            (folder/'pl_adapter_error.json').write_text(json.dumps({'error':repr(exc),'next_angle_cancelled':True,'driver_healthy':getattr(self.driver,'healthy',None),'retain_kernel_if_DMA_recovery_unconfirmed':True},indent=2))
            raise
        finally:(folder/'pl_transactions.json').write_text(json.dumps(transactions,indent=2))


class SwitchablePLCamera(PersistentPLCamera):
    """One camera session; each actual exposure has separate original PL evidence."""
    def configure_exposure(self,value):
        if type(value) is not int or value not in (5,20):raise ValueError('Only paired5/20 supported')
        if self.root is None or self.closed or self.uncertain or self.capture_blocked:raise RuntimeError('Camera not available for exposure switch')
        if value==self.exposure:return {'exposure':value,'unchanged':True}
        self.uncertain=True
        try:
            reply=self._request('set_exposure',exposure=value)
            if reply.get('exposure_set') is not True or reply.get('manual_controls_verified') is not True or reply.get('exposure')!=value or reply.get('session_id')!=self.session_id or reply.get('settle',{}).get('discarded_frames',0)<10 or reply.get('settle',{}).get('elapsed_s',0)<1.:
                raise RuntimeError('Exposure switch acknowledgement not verified')
            self.exposure=value;self.uncertain=False
            return reply
        except BaseException:
            self.capture_blocked=True
            raise
