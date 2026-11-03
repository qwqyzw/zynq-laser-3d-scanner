"""Finite Emm angle moves; safe imports. Direct mechanical coupling confirmed.

Feedback tolerances are workflow gates, not independent turntable accuracy.
PYNQ is imported only by the explicit hardware constructor.
"""
from dataclasses import dataclass, asdict
import math
import hashlib
from pathlib import Path
import time
from motor_arduino import ArduinoReadOnlyClient
from motor_readonly import ProtocolError


@dataclass(frozen=True)
class MotorGeometry:
    step_angle_deg: float
    microsteps: int
    motor_turns_per_table_turn: float = 1.0

    @property
    def degrees_per_pulse(self):
        return self.step_angle_deg/self.microsteps


def geometry_from_configuration(configuration):
    if configuration.get('firmware_family_layout') != 'Emm':
        raise ValueError('Only verified Emm configuration layout is supported')
    raw=bytes.fromhex(configuration['raw_configuration_hex'])
    if len(raw)!=33 or raw[1:4]!=bytes([0x42,33,21]) or raw[-1]!=0x6b:
        raise ValueError('Invalid Emm configuration')
    steps={0x19:1.8,0x32:0.9}
    if raw[4] not in steps or raw[6]!=2 or raw[18]!=0 or raw[21]!=0 or raw[22]!=1:
        raise ValueError('Need known motor type, UART,9600,fixed6B,Receive response mode; no settings changed')
    return MotorGeometry(steps[raw[4]],raw[9] or 256)


def plan_relative_move(delta_deg, geometry, rpm=1, max_step_deg=30):
    if not math.isfinite(max_step_deg) or not 0<max_step_deg<=30 or not math.isfinite(delta_deg) or abs(delta_deg)>max_step_deg:
        raise ValueError('Requested single move exceeds configured degree limit')
    if type(rpm) is not int or not 1<=rpm<=3:
        raise ValueError('Position tests support integer1..3RPM')
    if geometry.step_angle_deg not in (0.9,1.8) or type(geometry.microsteps) is not int or not 1<=geometry.microsteps<=256 or geometry.motor_turns_per_table_turn!=1:
        raise ValueError('Verified motor geometry and direct1:1 coupling required')
    pulses=math.floor(abs(delta_deg)/geometry.degrees_per_pulse+0.5)
    if pulses>1024:
        raise ValueError('Pulse count exceeds MicroBlaze limit1024')
    direction=0 if delta_deg>=0 else 1
    return {'requested_delta_deg':delta_deg,'pulses':pulses,'direction':direction,
            'rpm':rpm,'quantized_delta_deg':pulses*geometry.degrees_per_pulse*(1 if direction==0 else -1)}


def position_frame(address, plan):
    if type(address) is not int or not 1<=address<=255:
        raise ValueError('Unicast motor address required')
    if type(plan['pulses']) is not int or not 1<=plan['pulses']<=1024 or plan['direction'] not in (0,1) or type(plan['rpm']) is not int or not 1<=plan['rpm']<=3:
        raise ValueError('Invalid bounded position request')
    return bytes([address,0xfd,plan['direction'],0,plan['rpm'],10])+plan['pulses'].to_bytes(4,'big')+bytes([2,0,0x6b])


def wait_for_settle(read_status, read_position, target_deg, *, timeout_s=15,
                    interval_s=.2, stable_samples=3, stable_delta_deg=.1,
                    target_tolerance_deg=.3, minimum_settle_s=2.0,
                    clock=time.monotonic, sleep=time.sleep):
    if not math.isfinite(target_deg) or not 0<timeout_s<=60 or not .01<=interval_s<=1 or not 2<=stable_samples<=10 or not 0<stable_delta_deg<=1 or not 0<target_tolerance_deg<=1 or not 0<=minimum_settle_s<=5:
        raise ValueError('Invalid settle limits')
    deadline=clock()+timeout_s; samples=[]; good_since=None; window=[]
    while clock()<deadline:
        status=read_status(); position=read_position()
        if clock()>deadline:
            break
        value=position['motor_position_deg']
        if not math.isfinite(value):raise ProtocolError('Nonfinite motor position')
        samples.append({'position':position,'status':status,'sample_monotonic':clock()})
        if status['stall_detected'] or status['stall_protection_triggered']:
            raise ProtocolError('Motor reports stall/protection; stop scan')
        if not status['enabled']:
            raise ProtocolError('Motor disabled during positioning')
        good=status['position_reached'] and abs(value-target_deg)<=target_tolerance_deg
        if good:
            if good_since is None:good_since=clock()
            window=(window+[value])[-stable_samples:]
        else:
            good_since=None;window=[]
        stable=(len(window)>=stable_samples and max(window)-min(window)<=stable_delta_deg)
        # Arrival can be followed by slow closed-loop relaxation. Require dwell
        # plus a bounded whole sample window; do not loosen capture drift limits.
        if stable and clock()-good_since>=minimum_settle_s:
            return {'motor_position_deg':value,'target_deg':target_deg,'samples':samples,
                    'target_tolerance_deg':target_tolerance_deg,'stable_delta_deg':stable_delta_deg,
                    'minimum_settle_s':minimum_settle_s,'in_target_duration_s':clock()-good_since}
        sleep(min(interval_s,max(0,deadline-clock())))
    error=TimeoutError('Position did not reach target and settle within deadline')
    error.samples=samples
    raise error


class ArduinoAngleController(ArduinoReadOnlyClient):
    def __init__(self,overlay,address=1):
        root=Path(__file__).parent
        source='\n'.join((root/name).read_text() for name in ('motor_arduino_rpc.c','motor_arduino_position_rpc.c'))
        super().__init__(overlay,address,source)
        self.geometry=None;self.prepared=False;self.closed=False;self.motion_log=[]
        self.source_sha256=hashlib.sha256(source.encode()).hexdigest()
        self.overlay_bitfile=overlay.bitfile_name

    def _exchange(self,operation,plan=None):
        if self.closed:raise RuntimeError('Motor client already closed')
        if plan is None:plan={'direction':0,'pulses':0,'rpm':1}
        frame=(position_frame(self.address,plan) if operation==2 else
               bytes([self.address,0xfe,0x98,0,0x6b]) if operation==0 else
               bytes([self.address,0xf3,0xab,1,0,0x6b]))
        entry={'operation':operation,'tx_hex':frame.hex(' ')};self.motion_log.append(entry)
        buffer=[0]*64
        result=int(self.rpc.motor_position_exchange(self.address,operation,plan['direction'],plan['pulses'],plan['rpm'],buffer,64,500))
        count=int(self.rpc.motor_last_rx_length())
        if not 0<=count<=64:raise ProtocolError('Invalid receive length')
        raw=bytes(buffer[:count]);entry.update(return_code=result,rx_hex=raw.hex(' '),sent_bytes=int(self.rpc.motor_last_tx_length()),uart_status=int(self.rpc.motor_last_status()))
        if result!=4 or raw!=bytes([self.address,frame[1],2,0x6b]):
            raise ProtocolError('No valid command acceptance; command is not resent',raw)
        return entry

    def stop(self):
        return self._exchange(0)

    def position(self):
        return self.query('position',firmware_family='Emm')

    def status(self):
        return self.query('status')

    def prepare(self):
        configuration=self.query('configuration')
        self.geometry=geometry_from_configuration(configuration)
        self.stop()
        status=self.status()
        if status['stall_detected'] or status['stall_protection_triggered']:
            raise ProtocolError('Motor fault at preparation')
        if not status['enabled']:self._exchange(1)
        position=self.position()['motor_position_deg']
        settled=wait_for_settle(self.status,self.position,position)
        self.prepared=True
        return {'configuration':configuration,'geometry':asdict(self.geometry),
                'initial_position_deg':settled['motor_position_deg'],
                'initial_settle':settled,'voltage':self.query('voltage'),
                'backend':'hardware_pynq_uart','source_sha256':self.source_sha256,
                'overlay_bitfile':self.overlay_bitfile}

    def move_to(self,target_motor_deg,rpm=1):
        if not self.prepared:raise RuntimeError('Call prepare before moving')
        current=self.position()['motor_position_deg']
        plan=plan_relative_move(target_motor_deg-current,self.geometry,rpm)
        plan['requested_target_motor_deg']=target_motor_deg
        plan['quantized_target_motor_deg']=current+plan['quantized_delta_deg']
        if plan['pulses']:self._exchange(2,plan)
        settled=wait_for_settle(self.status,self.position,plan['quantized_target_motor_deg'])
        return {'plan':plan,'settled':settled,'motor_position_deg':settled['motor_position_deg']}

    def close(self):
        if self.closed:return
        try:self.stop()
        finally:
            self.closed=True
            super().close()
