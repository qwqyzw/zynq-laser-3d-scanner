"""Exact integer PS cross-check, restricted to ROI plus the 16-row halo.

No hardware and no real XYZ. Equations and tie rounding match frozen reference.
"""
import numpy as np
from laser_vertical_fast import keep_long_runs

def extract_integer(bgr,roi,threshold_q8=3072,min_run=8):
    if bgr.shape!=(480,640,3):raise ValueError('Need 480x640 BGR')
    x0,y0,x1,y1=map(int,roi)
    if not (0<=x0<x1<=640 and 0<=y0<y1<=480 and type(threshold_q8) is int and 0<=threshold_q8<=65535):raise ValueError('Invalid ROI/threshold')
    # D(y) needs y+-15; C(y) needs D(y+-1). Clip at the IMAGE edge.
    rows=np.clip(np.arange(y0-16,y1+16),0,479)
    crop=bgr[rows,x0:x1].astype(np.int64)
    t=2*crop[:,:,2]-crop[:,:,0]-crop[:,:,1]
    sums=np.pad(np.cumsum(t,axis=0,dtype=np.int64),((1,0),(0,0)))
    D=31*t[15:-15]-(sums[31:]-sums[:-31])
    # D outside image is replicated before C, as in the full reference.
    if y0==0:D[0]=D[1]
    if y1==480:D[-1]=D[-2]
    C=D[:-2]+2*D[1:-1]+D[2:]
    col=np.arange(x1-x0);peak=np.argmax(C,axis=0);peakC=C[peak,col]
    K=np.maximum(7440,9*peakC);left=np.zeros(len(col),int);right=left.copy()
    liveL=np.ones(len(col),bool);liveR=liveL.copy()
    for d in range(1,9):
        yy=peak-d;liveL&=(yy>=0)&(20*C[np.maximum(yy,0),col]>=K);left+=liveL
        yy=peak+d;liveR&=(yy<len(C))&(20*C[np.minimum(yy,len(C)-1),col]>=K);right+=liveR
    width=left+right+1
    valid=(peak>0)&(peak<len(C)-1)&(peakC*256>=threshold_q8*248)&(width>=2)&(width<=14)
    den=np.zeros(len(col),np.int64);num=den.copy()
    for d in range(-8,9):
        yy=peak+d;inside=(d>=-left)&(d<=right)
        w=np.maximum(20*C[np.clip(yy,0,len(C)-1),col]-K,0)*inside
        den+=w;num+=(yy+y0)*w
    valid&=den>0
    raw=np.zeros(640,bool);raw[x0:x1]=valid
    count=np.convolve(raw.astype(np.int32),np.ones(5,np.int32),mode='full')[2:642]
    keep=keep_long_runs(raw&(count>=3),min_run)
    q=np.zeros(640,np.uint32);q[x0:x1]=((2*num*256+den)//(2*np.maximum(den,1))).astype(np.uint32)
    words=np.zeros(640,np.uint32);words[keep]=np.uint32(0x80000000)|q[keep]
    return words
