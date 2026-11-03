"""Add compatible cleanup to frozen66; all actual scan work stays frozen61."""
from pathlib import Path
import argparse,json,sys
HERE=Path(__file__).resolve().parent;P=HERE.parent
sys.path.insert(0,str(P/'66_responsive_button_start_20261024'))
import responsive_button_service as base
from compatible_cancel import CompatibleCancelMixin

def verify(pin):
    if base.legacy.digest(HERE/'manifest.json')!=pin:raise ValueError('Compatible service manifest differs')
    data=base.legacy.load(HERE/'manifest.json')
    for name,wanted in data['files'].items():
        if Path(name).name!=name or (HERE/name).is_symlink() or base.legacy.digest(HERE/name)!=wanted:
            raise ValueError('Compatible service file differs: '+name)
    base.own_identity(data['base66_manifest_sha256'])
    return data

class Coordinator(CompatibleCancelMixin,base.Coordinator):
    def __init__(self,*args,compat_manifest_sha256,**kwargs):
        self.compat_manifest_sha256=compat_manifest_sha256
        super().__init__(*args,**kwargs)
    def start(self):
        verify(self.compat_manifest_sha256)
        super().start()
        if self.run is not None and self.process is not None:
            base.legacy.original_service.save(self.run/'compatibility_binding.json',{
                'manifest_sha256':self.compat_manifest_sha256,'cleanup_adapter':'61+65',
                'base66_manifest_sha256':self.startup_manifest_sha256})
    def _assert_discard_quiescent(self,path):
        verify(self.compat_manifest_sha256)
        return super()._assert_discard_quiescent(path)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True,type=Path)
    parser.add_argument('--compat-manifest-sha256',required=True)
    parser.add_argument('--load-board',action='store_true');parser.add_argument('--serve-board',action='store_true')
    args=parser.parse_args();identity=verify(args.compat_manifest_sha256)
    config,art=base.legacy.inspect_config(args.config);config['config_file']=args.config.resolve()
    if args.load_board:
        from load_button_overlay import load_board
        print(json.dumps(load_board(config,art)),flush=True)
    if args.serve_board:Coordinator(config,art,startup_manifest_sha256=identity['base66_manifest_sha256'],compat_manifest_sha256=args.compat_manifest_sha256).serve()
    elif not args.load_board:print(json.dumps({'hardware_accessed':False,'compatible_cleanup':'61+65','scan_release':'frozen61'}))
if __name__=='__main__':main()
