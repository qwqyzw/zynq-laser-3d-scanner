"""Offline simulation/control tests; never imports PYNQ or opens devices."""
from pathlib import Path
import sys,json,tempfile,shutil
from types import SimpleNamespace
from unittest.mock import patch
import cv2,numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'04_runtime'))
from scan_fixed_exposure import PerPoseExposureCamera
from pl_scan_camera import PLStripeCheckedCamera
from laser_horizontal_check import extract_horizontal
from laser_stripe import StripeResult
from scan_simulation import simulate
from rig_workflow import run_small_scan

class FakePL:
    def __init__(self,corrupt=False):self.count=0;self.healthy=True;self.corrupt=corrupt
    def run_words(self,words,config):
        self.count+=1;w=words.ravel();im=np.stack([w&255,(w>>8)&255,(w>>16)&255],axis=1).reshape(480,640,3).astype(np.uint8)
        ref=extract_horizontal(im,(config.x0,config.y0,config.x1,config.y1),config.threshold_q8/256,config.min_run)
        v=ref['valid'].copy();out=np.zeros(640,np.uint32);out[v]=np.uint32(0x80000000)|np.floor(ref['y'][v].astype(float)*256+.5).astype(np.uint32)
        if self.corrupt:out[:]=0;v[:]=False
        return StripeResult(out,v,np.where(v,(out&0x3ffff).astype(float)/256,np.nan),0,self.count)

def main():
    results=[]
    with tempfile.TemporaryDirectory(prefix='pynq_transfer_') as td:
        temp=Path(td)
        for case,group,corrupt,expected_success in [('valid','on_before',False,True),('blocked','blocked',False,False),('bad_PL','on_before',True,False)]:
            folder=temp/case;folder.mkdir();src=ROOT/'07_evidence/on_block_on/board_evidence'/group
            images=[]
            for i in range(3):
                p=folder/f'raw_{i:02}.png';shutil.copy2(src/p.name,p);images.append({'file':str(p)})
            camera=PLStripeCheckedCamera('',(160,215,410,270),FakePL(corrupt),verify_ps=True)
            success=False
            with patch.object(PerPoseExposureCamera,'capture',return_value={'source':'SIMULATED_CAPTURE_FOR_ADAPTER_TEST','images':images}):
                try:batch=camera.capture(folder);success=True
                except RuntimeError:pass
            assert success==expected_success,case
            if not success:assert camera.capture_blocked and (folder/'pl_adapter_error.json').exists()
            if case=='bad_PL':assert camera.driver.count==1
            results.append({'case':case,'expected_behavior_verified':True,'PL_calls_simulated':camera.driver.count})
        simulation=simulate(temp/'simulated_scan',angles=(0,5,0),max_angle_deg=10,max_points=3,client_session_date='2026-10-20')
        assert simulation['status']=='completed' and simulation['simulation'] and not simulation['real_hardware_pipeline_verified']
        assert simulation['cleanup']['motor_stop_confirmed']
        results.append({'case':'original_scan_simulation_0_5_0','expected_behavior_verified':True})
        with patch('rig_workflow.ArduinoAngleController',side_effect=AssertionError('UART constructor must not run')):
            try:run_small_scan(SimpleNamespace(),temp/'must_not_start',(160,215,410,270),client_session_date='2026-10-20',backend='PL',pl_driver=FakePL())
            except RuntimeError:pass
            else:raise AssertionError('Missing UART did not block motion')
        results.append({'case':'missing_UART_blocks_before_motor_constructor','expected_behavior_verified':True})
    print(json.dumps({'offline_only':True,'new_PL_or_camera_hardware_tested':False,'all_passed':True,'checks':results},indent=2))
if __name__=='__main__':main()
