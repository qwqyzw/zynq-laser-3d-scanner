"""Optional PS three-frame review reproduction; not a PL comparison target."""
from pathlib import Path
import json,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'reference'))
from scan_stripe_quality import common_stripe_pixels
manifest=json.loads((ROOT/'manifest.json').read_text());checks=[]
for group in manifest['three_frame_groups']:
    pixels,quality=common_stripe_pixels([ROOT/n for n in group['three_originals']],tuple(group['roi']))
    passed=len(pixels)==group['expected_ps_common_columns']
    if group.get('failed_acquisition_retained'):passed=passed and not quality['image_quality_gate_passed']
    checks.append({'sample':group['sample_name'],'common_columns':len(pixels),'expected':group['expected_ps_common_columns'],'acquisition_quality_gate_passed':quality['image_quality_gate_passed'],'reproduced':bool(passed)})
result={'all_groups_reproduced':all(x['reproduced'] for x in checks),'groups':checks,'not_a_PL_output_comparison':True}
print(json.dumps(result,ensure_ascii=False,indent=2))
raise SystemExit(0 if result['all_groups_reproduced'] else 1)
