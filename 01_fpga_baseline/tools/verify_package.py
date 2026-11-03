"""Verify original image hashes, input packing and frozen float expected files offline."""
from pathlib import Path
import hashlib,json,sys
import numpy as np
from PIL import Image
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'reference'))
from laser_horizontal_check import extract_horizontal
from horizontal_vectors import read_words,format_errors,compare
from integer_model import extract_integer

def verify():
    m=json.loads((ROOT/'manifest.json').read_text());cases=[]
    for sample in m['samples']:
        image=ROOT/'images'/sample['name'];assert hashlib.sha256(image.read_bytes()).hexdigest()==sample['sha256']
        rgb=np.asarray(Image.open(image).convert('RGB'));assert rgb.shape==(480,640,3)
        bgr=rgb[:,:,::-1].copy();stem=Path(sample['name']).stem
        a=read_words(ROOT/'vectors'/(stem+'_input.bin'));b=read_words(ROOT/'vectors'/(stem+'_input.hex'))
        assert a.shape==(307200,) and np.array_equal(a,b) and not (a>>24).any()
        decoded=np.stack([a&255,(a>>8)&255,(a>>16)&255],axis=1).reshape(480,640,3).astype(np.uint8);assert np.array_equal(decoded,bgr)
        for mode,min_run in [('core',0),('full',8)]:
            exp=read_words(ROOT/'expected'/(stem+'_'+mode+'.hex'));assert np.array_equal(exp,read_words(ROOT/'expected'/(stem+'_'+mode+'.bin'))) and not format_errors(exp).any()
            reference=extract_horizontal(bgr,tuple(sample['roi']),sample['threshold'],min_run)
            words=np.zeros(640,np.uint32);valid=reference['valid'];words[valid]=np.uint32(0x80000000)|np.floor(reference['y'][valid].astype(float)*256+.5).astype(np.uint32)
            assert np.array_equal(words,exp)
            integer=extract_integer(bgr,tuple(sample['roi']),int(sample['threshold']*256+.5),min_run)
            check,_=compare(integer,exp,.25)
            cases.append({'sample':stem,'mode':mode,'expected_valid_columns':int(valid.sum()),'integer_model_vs_float':check})
    sha_path=ROOT/'SHA256.json';hash_count=0
    if sha_path.exists():
        for item in json.loads(sha_path.read_text())['files']:
            p=ROOT/item['path'];assert p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()==item['sha256'],item['path'];hash_count+=1
    return {'input_cases':len(m['samples']),'mode_cases':len(cases),'source_image_packing_and_float_reference_passed':True,'integer_model_comparison_passed':all(x['integer_model_vs_float']['passed'] for x in cases),'file_hashes_checked':hash_count,'rtl_simulated':False,'board_tested':False,'cases':cases}
if __name__=='__main__':
    result=verify();print(json.dumps({k:v for k,v in result.items() if k!='cases'},ensure_ascii=False,indent=2))
    if '--report' in sys.argv:Path(sys.argv[sys.argv.index('--report')+1]).write_text(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['integer_model_comparison_passed'] else 1)
