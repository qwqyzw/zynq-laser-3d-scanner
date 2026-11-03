"""Integer-scaled mathematical model for hardware design; not RTL or FPGA output.

score = C/248, cutoff weights use20*C-max(7440,9*C_peak).
Compared against frozen float32 reference separately; do not assume bit identity.
"""
import numpy as np
from laser_vertical_fast import keep_long_runs

def extract_integer(bgr,roi,threshold_q8=3072,min_run=8):
    assert bgr.shape==(480,640,3)
    x0,y0,x1,y1=map(int,roi)
    assert 0<=x0<x1<=640 and 0<=y0<y1<=480
    assert type(threshold_q8) is int and 0<=threshold_q8<=65535
    t=2*bgr[:,:,2].astype(np.int64)-bgr[:,:,0].astype(np.int64)-bgr[:,:,1].astype(np.int64)
    padded=np.pad(t,((15,15),(0,0)),mode='edge')
    sums=np.pad(np.cumsum(padded,axis=0,dtype=np.int64),((1,0),(0,0)))
    D=31*t-(sums[31:]-sums[:-31])
    p=np.pad(D,((1,1),(0,0)),mode='edge')
    C=p[:-2]+2*D+p[2:]
    raw_valid=np.zeros(640,bool);q8=np.zeros(640,np.uint32)
    for x in range(x0,x1):
        col=C[y0:y1,x];peak=int(np.argmax(col));peakC=int(col[peak])
        if peak==0 or peak==len(col)-1 or peakC*256<threshold_q8*248:continue
        K=max(7440,9*peakC);left=right=0
        for d in range(1,9):
            if peak-d<0 or 20*int(col[peak-d])<K:break
            left=d
        for d in range(1,9):
            if peak+d>=len(col) or 20*int(col[peak+d])<K:break
            right=d
        if not 2<=left+right+1<=14:continue
        den=num=0
        for y in range(peak-left,peak+right+1):
            weight=max(20*int(col[y])-K,0)
            den+=weight;num+=(y+y0)*weight
        if den<=0:continue
        raw_valid[x]=True;q8[x]=(2*num*256+den)//(2*den)
    count=np.convolve(raw_valid.astype(np.int32),np.ones(5,np.int32),mode='full')[2:642]
    valid=keep_long_runs(raw_valid&(count>=3),min_run)
    words=np.zeros(640,np.uint32);words[valid]=np.uint32(0x80000000)|q8[valid]
    return words
