"""Compare DUT files; missing outputs fail. No hardware access or vector creation."""
from pathlib import Path
import argparse,json
import numpy as np
from horizontal_vectors import read_words,compare
ROOT=Path(__file__).resolve().parents[1]
def main():
    ap=argparse.ArgumentParser();ap.add_argument('actual_dir',type=Path);ap.add_argument('--mode',choices=['core','full','both'],default='both');ap.add_argument('--tolerance',type=float,default=.25);ap.add_argument('--out',type=Path,default=Path('comparison_report'));args=ap.parse_args()
    args.out.mkdir(parents=True,exist_ok=True);rows=[]
    for sample in json.loads((ROOT/'manifest.json').read_text())['samples']:
        stem=Path(sample['name']).stem
        for mode in (['core','full'] if args.mode=='both' else [args.mode]):
            name=stem+'_'+mode;actual=args.actual_dir/(name+'.hex')
            try:
                report,columns=compare(read_words(actual),read_words(ROOT/'expected'/(name+'.hex')),args.tolerance)
                np.savetxt(args.out/(name+'.csv'),columns,delimiter=',',fmt=['%d','%d','%d','%.6f','%.6f','%.6f','%d','%d'],header='x,actual_valid,expected_valid,actual_y,expected_y,error_px,format_error,over_tolerance',comments='')
            except (ValueError,OSError) as exc:report={'passed':False,'error':str(exc)}
            rows.append({'sample':stem,'mode':mode,'actual_file':str(actual),**report})
    result={'passed':all(x['passed'] for x in rows),'compared_cases':len(rows),'failed_cases':sum(not x['passed'] for x in rows),'tolerance_px':args.tolerance,'results':rows,'note':'DUT results required; reference self-comparison is only a software-tool check'}
    (args.out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='results'},ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['passed'] else 1)
if __name__=='__main__':main()
