"""Clearly labelled offline fixtures for the acquisition pipeline, no hardware."""
import hashlib
from pathlib import Path
from motor_position import MotorGeometry, plan_relative_move
from rotation_scan import run_scan


class SimulatedMotor:
    def __init__(self):
        self.value=12.0;self.motion_log=[];self.transactions=[];self.closed=False
        self.geometry=MotorGeometry(1.8,16)

    def prepare(self):
        return {'initial_position_deg':self.value,'geometry':{'step_angle_deg':1.8,'microsteps':16},
                'source':'SIMULATION_NOT_HARDWARE'}

    def move_to(self,target_motor_deg,rpm=1):
        plan=plan_relative_move(target_motor_deg-self.value,self.geometry,rpm)
        self.value+=plan['quantized_delta_deg']
        self.motion_log.append(plan)
        return {'plan':plan,'motor_position_deg':self.value,'source':'SIMULATION_NOT_HARDWARE'}

    def position(self):return {'motor_position_deg':self.value}
    def stop(self):return {'source':'SIMULATION_NOT_HARDWARE','stop_simulated':True}
    def close(self):self.closed=True


class SimulatedCamera:
    def __init__(self,motor):self.motor=motor
    def start(self,root):return {'source':'SIMULATION_NOT_HARDWARE'}
    def capture(self,folder):
        import cv2
        import numpy as np
        folder=Path(folder);folder.mkdir(parents=True,exist_ok=False);images=[]
        for i in range(3):
            image=np.full((480,640,3),245,dtype=np.uint8)
            cv2.putText(image,'SIMULATION ONLY - NOT CAMERA',(20,80),cv2.FONT_HERSHEY_SIMPLEX,.8,(0,0,220),2)
            cv2.putText(image,'motor feedback %.4f deg'%self.motor.value,(20,170),cv2.FONT_HERSHEY_SIMPLEX,.7,(0,0,0),2)
            cv2.putText(image,'synthetic frame %d'%i,(20,240),cv2.FONT_HERSHEY_SIMPLEX,.7,(0,0,0),2)
            path=folder/('raw_%02d.png'%i)
            if not cv2.imwrite(str(path),image):raise RuntimeError('Simulation image write failed')
            images.append({'file':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'shape':list(image.shape)})
        return {'images':images,'source':'SIMULATION_NOT_HARDWARE'}
    def close(self):return {'camera_release_confirmed':True,'source':'SIMULATION_NOT_HARDWARE'}


def simulate(output_dir,**options):
    motor=SimulatedMotor()
    return run_scan(motor,SimulatedCamera(motor),output_dir,simulation=True,**options)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True)
    args=parser.parse_args()
    result=simulate(args.output)
    print('SIMULATION ONLY:',result['status'],result['output_dir'])
