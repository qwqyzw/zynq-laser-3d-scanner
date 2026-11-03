"""Observe original debounce decisions; never introduce new button actions."""
import time
from collections import deque

class AuditedGate:
 def __init__(self,gate,callback):
  self.gate=gate;self.callback=callback;self.last_bits=None;self.history=deque(maxlen=64);self.last_action=None
 def __getattr__(self,name):return getattr(self.gate,name)
 def update(self,bits,now,busy):
  bits=int(bits)&15;changed=bits!=self.last_bits
  if changed:self.history.append({'monotonic_s':now,'bits':bits,'busy':bool(busy)});self.last_bits=bits
  action=self.gate.update(bits,now,busy)
  if changed or action:
   entry={'kind':'gpio_action' if action else 'gpio_transition','monotonic_s':now,'raw_bits':bits,
          'stable_bits':self.gate.stable,'busy':bool(busy),'action':action,
          'debounce_seconds':self.gate.debounce,'hold_seconds':self.gate.hold,
          'stable_for_seconds':max(0,now-self.gate.changed)}
   if action:entry['recent_transitions']=list(self.history);self.last_action=entry
   self.callback(entry)
  return action
