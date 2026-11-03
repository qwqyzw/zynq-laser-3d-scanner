"""Exact BGR to0x00RRGGBB packing, optionally into preallocated uint32 storage.

This module never allocates a PYNQ buffer, invokes DMA, or changes cache state.
Caller must exclusively own an idle destination and flush before DMA as usual.
"""
import sys
import numpy as np

def pack_bgr_into(image,destination):
    if sys.byteorder!='little':raise RuntimeError('Byte packing requires little endian')
    if not isinstance(image,np.ndarray) or image.dtype!=np.dtype(np.uint8):
        raise TypeError('BGR image must be a numpy uint8 array')
    if image.shape!=(480,640,3):raise ValueError('Need BGR[480,640,3]')
    if not isinstance(destination,np.ndarray) or destination.dtype!=np.dtype(np.uint32):
        raise TypeError('Destination must be a numpy uint32 array')
    if destination.shape not in ((480,640),(307200,)):
        raise ValueError('Destination must contain307200 words')
    if not destination.flags.c_contiguous or not destination.flags.writeable:
        raise ValueError('Destination must be contiguous and writable')
    if np.shares_memory(image,destination):raise ValueError('Input and output must not alias')
    target=destination.view(np.uint8).reshape(480,640,4)
    # Separate planes avoid the slow3-to4-byte strided bulk-copy loop.
    target[:,:,0]=image[:,:,0];target[:,:,1]=image[:,:,1]
    target[:,:,2]=image[:,:,2];target[:,:,3]=0
    return destination

def pack_bgr_fast(image):
    destination=np.empty((480,640),dtype=np.uint32)
    return pack_bgr_into(image,destination)
