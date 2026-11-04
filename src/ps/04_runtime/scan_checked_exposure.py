"""Image quality gate with explicit exposure; legacy PS default remains 20.

Use exposure=5 for the scene measured on 2026-10-20. The same image-quality
rules apply to either setting; no height-based filtering or automatic retry.
"""
import json
from pathlib import Path
import numpy as np
from scan_fixed_exposure import PerPoseExposureCamera
from scan_stripe_quality import common_stripe_pixels


class StripeCheckedExposureCamera(PerPoseExposureCamera):
    def __init__(self, worker_source, roi, exposure=20, timeout_s=40):
        super().__init__(worker_source, exposure, timeout_s)
        self.roi=tuple(roi)

    def capture(self, folder):
        batch=super().capture(folder)
        pixels,quality=common_stripe_pixels([item['file'] for item in batch['images']],self.roi)
        (Path(folder)/'stripe_quality.json').write_text(json.dumps(quality,indent=2))
        np.savetxt(Path(folder)/'stripe_pixels.csv',pixels,delimiter=',',header='x_px,y_px',comments='')
        if not quality['image_quality_gate_passed']:
            self.capture_blocked=True
            raise RuntimeError('Laser stripe quality insufficient; next angle cancelled; no threshold changes or retry')
        batch['stripe_quality']=quality
        batch['stripe_pixels_file']=str(Path(folder).resolve()/'stripe_pixels.csv')
        return batch
