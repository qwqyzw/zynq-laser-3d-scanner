"""Frozen61 scan -> Zynq derived mesh -> compatible receiver; no new motion."""
from pathlib import Path
import argparse,json,sys
HERE=Path(__file__).resolve().parent;P=HERE.parent
sys.path.insert(0,str(P/'68_compatible_cancel_20261024'))
import compatible_button_service as base
original=base.base.legacy.original_service

def verify(pin):
    if original.digest(HERE/'manifest.json')!=pin:raise ValueError('Visual pipeline manifest differs')
    data=original.load(HERE/'manifest.json')
    for rel,wanted in data['project_files'].items():
        p=P/rel
        if not p.resolve().is_relative_to(P.resolve()) or p.is_symlink() or original.digest(p)!=wanted:
            raise ValueError('Visual pipeline source changed: '+rel)
    return base.verify(data['base68_manifest_sha256']),data

class Coordinator(base.Coordinator):
    def __init__(self,*args,visual_manifest_sha256,**kwargs):
        self.visual_manifest_sha256=visual_manifest_sha256
        super().__init__(*args,**kwargs)
    def start(self):verify(self.visual_manifest_sha256);return super().start()
    def request_stop(self):
        super().request_stop()
        if self.process is not None and self.stage=='surface_modelling_on_zynq' and self.process.poll() is None:
            # This child is CPU/file-only; scan camera/DMA have already closed.
            self.process.terminate()
    def poll(self):
        if self.stage!='surface_modelling_on_zynq':return super().poll()
        if self.process is None or self.process.poll() is None:return
        self.surface_returncode=self.process.returncode;self.process.stdin.close();self.process=None
        self.log.close();self.log=None
        if self.cancelled:return self.finish('failed','User stop requested')
        return self.finish('ready_diagnostic_preview')
    def finish(self,state,error=None):
        if self.cancelled or error is not None:return super().finish(state,error)
        if self.stage=='reconstructing_on_zynq':
            verify(self.visual_manifest_sha256)
            args=[sys.executable,'-B','-u',str(HERE/'model_after_scan.py'),'--run',str(self.run)]
            return original.Coordinator.launch(self,args,'surface_modelling_on_zynq')
        # Publish one atomic completion after model generation; raw measured
        # export remains separate and retains its original provenance.
        viewer=self.run/'model/viewer';manifest=original.load(viewer/'export_manifest.json');meta=original.load(viewer/'source_meta.json')
        if manifest.get('coordinate_producer')!='zynq' or manifest.get('test_only') is not False:raise ValueError('Board provenance changed')
        record={'schema_version':1,'session_id':self.session_id,'run_id':self.run_id,'state':'ready',
            'result_kind':'diagnostic_preview','diagnostic_preview_only':True,'alignment_validated':False,'metric_accuracy_accepted':False,
            'viewer_relative':viewer.relative_to(self.root).as_posix(),'export_manifest_sha256':original.digest(viewer/'export_manifest.json'),
            'point_count':manifest['point_statistics']['finite_points'],'visual_pipeline_manifest_sha256':self.visual_manifest_sha256}
        for k in ['diagnostic_preview_only','alignment_validated','metric_accuracy_accepted']:
            if type(meta.get(k)) is not bool or meta[k]!=record[k]:raise ValueError('Diagnostic status changed')
        surface=self.run/'model/visual_model/artifact_manifest.json'
        if getattr(self,'surface_returncode',1)==0 and surface.is_file():
            record.update(surface_relative=surface.parent.relative_to(self.root).as_posix(),surface_manifest_sha256=original.digest(surface),surface_kind='estimated_convex_surface')
        else:record.update(surface_unavailable=True,surface_note='Raw measured pointcloud retained; inspect model/surface_job.json')
        original.save(self.run/'result.json',record);original.save(self.root/'latest_result.json',record)
        self.status(state,surface_kind=record.get('surface_kind'),surface_unavailable=record.get('surface_unavailable',False))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True,type=Path)
    parser.add_argument('--visual-manifest-sha256',required=True);parser.add_argument('--load-board',action='store_true');parser.add_argument('--serve-board',action='store_true')
    args=parser.parse_args();identity,binding=verify(args.visual_manifest_sha256)
    cfg,art=base.base.legacy.inspect_config(args.config);cfg['config_file']=args.config.resolve()
    if args.load_board:
        from load_button_overlay import load_board
        print(json.dumps(load_board(cfg,art)),flush=True)
    if args.serve_board:Coordinator(cfg,art,startup_manifest_sha256=identity['base66_manifest_sha256'],
        compat_manifest_sha256=binding['base68_manifest_sha256'],visual_manifest_sha256=args.visual_manifest_sha256).serve()
    elif not args.load_board:print(json.dumps({'hardware_accessed':False,'pipeline':'frozen61->Zynq denoise+estimated surface->PC'}))
if __name__=='__main__':main()
