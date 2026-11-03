"""Remove this receiver's cancelled scan and close only its verified viewer PIDs."""
from pathlib import Path
import os,re,shutil


def remove_child(root,target):
    root,target=Path(root).resolve(),Path(target)
    if target.is_symlink() or target.resolve().parent!=root:
        raise ValueError('Refusing deletion outside expected direct output child')
    if target.exists():
        for p in target.rglob('*'):
            if p.is_symlink() or not p.resolve().is_relative_to(root):
                raise ValueError('Linked/escaped scan output is not disposable')
        shutil.rmtree(target)


def close_owned_viewer(receipt):
    pid=receipt.get('cloudcompare_pid');expected=receipt.get('cloudcompare_executable')
    if os.name!='nt' or type(pid) is not int or not expected:return False
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
    kernel.TerminateProcess.argtypes=[wintypes.HANDLE,wintypes.UINT]
    handle=kernel.OpenProcess(0x1001,False,pid)
    if not handle:return False
    try:
        size=wintypes.DWORD(32768);buffer=ctypes.create_unicode_buffer(size.value)
        if not kernel.QueryFullProcessImageNameW(handle,0,buffer,ctypes.byref(size)):return False
        if Path(buffer.value).resolve()!=Path(expected).resolve():return False
        return bool(kernel.TerminateProcess(handle,0))
    finally:kernel.CloseHandle(handle)


def discard_local(identity,receipts,output):
    if not re.fullmatch('[0-9a-f]{32}_[0-9a-f]{32}',identity):raise ValueError('Invalid discard identity')
    closed=sum(close_owned_viewer(v) for v in receipts.values())
    remove_child(Path(output),Path(output)/identity)
    receipt=receipts.pop(identity,{})
    manifest=receipt.get('manifest_sha256')
    if os.environ.get('LOCALAPPDATA') and isinstance(manifest,str) and re.fullmatch('[0-9a-f]{64}',manifest):
        base=Path(os.environ['LOCALAPPDATA'])/'ZynqPointCloud'
        for folder in ['cloudcompare_cache','display_receipts']:
            remove_child(base/folder,base/folder/manifest)
    return {'cancelled_scan_removed':identity,'owned_viewers_closed':closed,'pc_coordinate_computation':False}
