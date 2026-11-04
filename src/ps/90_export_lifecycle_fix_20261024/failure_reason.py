"""Translate original failed acquisition records without altering evidence."""
def classify(trial):
 poses=trial.get('poses',[])
 for key,phase in [('fixed_before','扫描前'),('fixed_after','扫描后')]:
  ref=trial.get(key,{})
  if ref.get('all_passed') is False:
   patches=ref.get('patches',[]);shift=max((p.get('shift_px',0) for p in patches),default=0);corr=min((p.get('correlation',1) for p in patches),default=1)
   return {'code':'fixed_scene_reference_failed','phase':key,'completed_poses':len(poses),'export_expected':False,'maximum_shift_px':shift,'minimum_correlation':corr,'message':f'{phase}固定背景检查未通过：最大偏移{shift:.2f}像素（上限2），最低相似度{corr:.3f}（要求0.900）。'+('本轮尚未采集扫描数据，不会生成模型。' if not poses else '本轮未发布结果。')+'请保持相机、激光及线缆固定，并检查固定标记是否被遮挡。'}
 original=trial.get('error','')
 if isinstance(original,str) and original.strip():return {'code':'acquisition_failed','completed_poses':len(poses),'export_expected':False,'message':original}
 trace=trial.get('traceback','')
 detail=next((line.strip() for line in reversed(trace.splitlines()) if line.strip()),'未提供异常信息')
 return {'code':'acquisition_failed_without_message','completed_poses':len(poses),'export_expected':False,'message':f'采集程序失败（{detail}），已完成{len(poses)}个姿态；结果未导出，请检查本轮采集记录。'}
