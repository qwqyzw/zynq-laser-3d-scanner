from pathlib import Path
import hashlib,json,sys
D=Path(__file__).resolve().parent;P=D.parent
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def verify_release(pin):
 if sha(D/'manifest.json')!=pin:raise ValueError('Dual button manifest changed')
 data=json.loads((D/'manifest.json').read_text())
 for name,h in data['files'].items():
  path=D/name
  if Path(name).name!=name or path.is_symlink() or sha(path)!=h:raise ValueError('Dual source changed: '+name)
 sys.path.insert(0,str(P/'73_pointcloud_review_first_20261024'))
 import review_first_service
 review_first_service.verify(data['base73_manifest_sha256'])
 for rel,h in data['inherited_files'].items():
  path=P/rel
  if not path.resolve().is_relative_to(P.resolve()) or path.is_symlink() or sha(path)!=h:raise ValueError('Inherited source changed: '+rel)
 return data
