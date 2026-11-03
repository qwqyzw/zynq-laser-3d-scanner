"""Commissioning mode: publish reviewed raw data, defer automatic hull fitting."""
from pathlib import Path
import argparse,json,sys
HERE=Path(__file__).resolve().parent;P=HERE.parent
sys.path.insert(0,str(P/'71_automatic_surface_pipeline_20261024'))
import visual_button_service as base
from pointcloud_review import review
original=base.original

def verify(pin):
    if original.digest(HERE/'manifest.json')!=pin:raise ValueError('Pointcloud review manifest changed')
    data=original.load(HERE/'manifest.json')
    for n,h in data['files'].items():
        if Path(n).name!=n or (HERE/n).is_symlink() or original.digest(HERE/n)!=h:raise ValueError('Pointcloud review source changed')
    identity,binding=base.verify(data['base71_manifest_sha256']);return data,identity,binding

class Coordinator(base.Coordinator):
    def __init__(self,*args,review_manifest_sha256,**kwargs):
        self.review_manifest_sha256=review_manifest_sha256;super().__init__(*args,**kwargs)
    def start(self):verify(self.review_manifest_sha256);return super().start()
    def finish(self,state,error=None):
        if self.cancelled or error is not None:return super().finish(state,error)
        if self.stage!='reconstructing_on_zynq':return super().finish(state,error)
        verify(self.review_manifest_sha256);assessment=review(self.run)
        viewer=self.run/'model/viewer';manifest=original.load(viewer/'export_manifest.json');metadata=original.load(viewer/'source_meta.json')
        if manifest.get('coordinate_producer')!='zynq' or manifest.get('test_only') is not False:raise ValueError('Actual Zynq data required')
        record={'schema_version':1,'session_id':self.session_id,'run_id':self.run_id,'state':'ready',
          'result_kind':'diagnostic_preview','diagnostic_preview_only':True,'alignment_validated':False,'metric_accuracy_accepted':False,
          'viewer_relative':viewer.relative_to(self.root).as_posix(),'export_manifest_sha256':original.digest(viewer/'export_manifest.json'),
          'point_count':manifest['point_statistics']['finite_points'],'surface_review_required':True,
          'surface_note':'Surface construction deferred until new measured pointcloud and object shape are reviewed',
          'review_manifest_sha256':original.digest(self.run/'model/pointcloud_review/review_manifest.json'),
          'review_relative':(self.run/'model/pointcloud_review').relative_to(self.root).as_posix()}
        for k in ['diagnostic_preview_only','alignment_validated','metric_accuracy_accepted']:
            if type(metadata.get(k)) is not bool or metadata[k]!=record[k]:raise ValueError('Diagnostic identity changed')
        original.save(self.run/'result.json',record);original.save(self.root/'latest_result.json',record)
        self.status(state,surface_review_required=True,empty_unique_angles_deg=assessment['empty_unique_angles_deg'])

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True,type=Path);parser.add_argument('--review-manifest-sha256',required=True)
    parser.add_argument('--load-board',action='store_true');parser.add_argument('--serve-board',action='store_true');args=parser.parse_args()
    data,identity,binding=verify(args.review_manifest_sha256);cfg,art=base.base.base.legacy.inspect_config(args.config);cfg['config_file']=args.config.resolve()
    if args.load_board:
        from load_button_overlay import load_board
        print(json.dumps(load_board(cfg,art)),flush=True)
    if args.serve_board:Coordinator(cfg,art,review_manifest_sha256=args.review_manifest_sha256,
         visual_manifest_sha256=data['base71_manifest_sha256'],compat_manifest_sha256=binding['base68_manifest_sha256'],
         startup_manifest_sha256=identity['base66_manifest_sha256']).serve()
    elif not args.load_board:print(json.dumps({'hardware_accessed':False,'surface_construction':'deferred_pending_new_pointcloud_review'}))
if __name__=='__main__':main()
