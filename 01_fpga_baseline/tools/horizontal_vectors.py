"""Generate and compare horizontal-stripe, 640-column Q8 records offline."""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT / 'reference' if (ROOT/'reference').is_dir() else ROOT))
from laser_horizontal_check import extract_horizontal, save_horizontal_csv


def read_words(path):
    path=Path(path)
    if path.suffix.lower()=='.bin':
        if path.stat().st_size%4: raise ValueError('二进制长度必须是4的倍数')
        return np.fromfile(path,dtype='<u4')
    lines=[s.split('//')[0].strip() for s in path.read_text(encoding='utf-8').splitlines()]
    values=[int(s,16) for s in lines if s]
    if any(v<0 or v>0xffffffff for v in values): raise ValueError('超出32位无符号范围')
    return np.asarray(values,dtype=np.uint32)


def format_errors(words):
    if words.shape!=(640,): raise ValueError('必须恰好640条列记录，包括无效列')
    valid=(words>>31).astype(bool)
    coord=(words&0x3ffff).astype(float)/256
    return ((words&0x7ffc0000)!=0)|((~valid)&(words!=0))|(valid&(coord>=480))


def compare(actual,expected,tolerance=0.25):
    if not np.isfinite(tolerance) or tolerance<0: raise ValueError('比较容差必须非负且有限')
    bad=format_errors(actual)
    if format_errors(expected).any(): raise ValueError('参考记录本身格式非法')
    av=(actual>>31).astype(bool); ev=(expected>>31).astype(bool)
    ay=(actual&0x3ffff).astype(float)/256; ey=(expected&0x3ffff).astype(float)/256
    common=av&ev
    error=np.full(640,np.nan); error[common]=np.abs(ay[common]-ey[common])
    mismatch=av!=ev; over=common&(error>tolerance)
    report={'passed':not (bad.any() or mismatch.any() or over.any()),
            'actual_valid_columns':int(av.sum()),'expected_valid_columns':int(ev.sum()),
            'format_error_columns':np.flatnonzero(bad).tolist(),
            'valid_mismatch_columns':np.flatnonzero(mismatch).tolist(),
            'over_tolerance_columns':np.flatnonzero(over).tolist(),
            'max_common_error_px':float(error[common].max()) if common.any() else None,
            'tolerance_px':float(tolerance)}
    rows=np.column_stack([np.arange(640),av.astype(int),ev.astype(int),
                          np.where(av,ay,np.nan),np.where(ev,ey,np.nan),error,bad.astype(int),over.astype(int)])
    return report,rows


def generate(root):
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    for sample in manifest['samples']:
        rgb=np.asarray(Image.open(root/'images'/sample['name']).convert('RGB'))
        if rgb.shape!=(480,640,3): raise ValueError('输入原图必须为640×480')
        bgr=rgb[:,:,::-1].copy()
        words=(bgr[:,:,0].astype(np.uint32)|(bgr[:,:,1].astype(np.uint32)<<8)|
               (bgr[:,:,2].astype(np.uint32)<<16)).ravel()
        stem=Path(sample['name']).stem
        input_path=root/'vectors'/(stem+'_input')
        words.astype('<u4').tofile(str(input_path)+'.bin')
        np.savetxt(str(input_path)+'.hex',words,fmt='%08x')
        recovered=np.fromfile(str(input_path)+'.bin',dtype='<u4').reshape(480,640)
        restored=np.stack([recovered&255,(recovered>>8)&255,(recovered>>16)&255],axis=2).astype(np.uint8)
        np.testing.assert_array_equal(restored,bgr)
        sample['valid_columns']={}
        for mode,min_run in [('core',0),('full',8)]:
            result=extract_horizontal(bgr,tuple(sample['roi']),sample['threshold'],min_run)
            encoded=np.zeros(640,dtype=np.uint32)
            valid=result['valid']
            q8=np.floor(result['y'][valid].astype(float)*256+0.5).astype(np.uint32)
            encoded[valid]=(np.uint32(1)<<np.uint32(31))|q8
            if format_errors(encoded).any(): raise ValueError('生成参考记录格式错误')
            path=root/'expected'/(stem+'_'+mode)
            encoded.astype('<u4').tofile(str(path)+'.bin')
            np.savetxt(str(path)+'.hex',encoded,fmt='%08x')
            save_horizontal_csv(str(path)+'.csv',result)
            sample['valid_columns'][mode]=int(valid.sum())
        print(stem,sample['valid_columns'])
    (root/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    gen=sub.add_parser('generate'); gen.add_argument('--root',type=Path,default=ROOT)
    cmp=sub.add_parser('compare'); cmp.add_argument('actual',type=Path); cmp.add_argument('expected',type=Path)
    cmp.add_argument('--tolerance',type=float,default=0.25)
    cmp.add_argument('--report',type=Path,default=Path('comparison_columns.csv'))
    args=parser.parse_args()
    try:
        if args.action=='generate': generate(args.root); return
        r,rows=compare(read_words(args.actual),read_words(args.expected),args.tolerance)
        np.savetxt(args.report,rows,delimiter=',',fmt=['%d','%d','%d','%.6f','%.6f','%.6f','%d','%d'],
                   header='x,actual_valid,expected_valid,actual_y,expected_y,error_px,format_error,over_tolerance',comments='')
        print(json.dumps(r,ensure_ascii=False,indent=2))
        raise SystemExit(0 if r['passed'] else 1)
    except (ValueError,OSError) as exc: parser.exit(2,str(exc)+'\n')


if __name__=='__main__': main()
