"""File-only session proof: per-batch streaming is not per-batch restoration."""
def verify_session(root,scan,digest,load,require):
    folder=root/'camera_session';start=load(folder/'session_start.json');close=load(folder/'session_close.json')
    require(start['schema']==close['schema']=='persistent_manual_exposure_v1','Wrong persistent camera schema')
    require(start['session_id']==close['session_id']==scan['camera_ready']['session_id'],'Camera session identity changed')
    require(start['manual_controls_verified'] is True and start['camera_open_count']==1 and start['exposure']==scan['exposure'],
            'Initial manual camera exposure/mode not verified')
    require(start['mode']['fourcc']=='MJPG' and start['mode']['width']==640 and start['mode']['height']==480
            and abs(start['mode']['fps_readback']-10)<.1,'Persistent camera mode differs')
    require(close['camera_release_confirmed'] is True and close['exposure_restoration_verified'] is True
            and close['explicit_close_received'] is True and close['restore_errors']==[] and close['error'] is None,
            'Persistent final restoration/release not verified')
    require(close['captured_batches']==scan['plan']['expected_capture_batches']==75,
            'Camera final capture count differs')
    require(close['original_controls']==start['original_controls'],'Original camera controls changed')
    for key in ['ae','exp']:require(close['restored_controls'][key]==start['original_controls'][key],'Final exposure differs')
    require(start['white_balance_gain_written'] is False and close['white_balance_gain_written'] is False,'Unexpected colour writes')
    require(digest(root/'persistent_camera_worker.py')==start['worker_sha256']==close['worker_sha256'],
            'Persistent worker bytes changed')
    require(digest(folder/'session_close.json')==scan['cleanup']['camera']['session_close_sha256'],
            'Final camera receipt binding differs')
    files={p.relative_to(root).as_posix():digest(p) for p in [folder/'session_start.json',folder/'session_close.json',root/'persistent_camera_worker.py']}
    require(len(close['bright_images'])==6,'Need actual start/end bright originals')
    for phase in ['bright_before_session','bright_after_session']:
        rows=[r for r in close['bright_images'] if r['phase']==phase]
        require([r['file'] for r in rows]==['bright_%02d.png'%i for i in range(3)],'Bright frame identities differ')
        for row in rows:
            p=folder/phase/row['file'];require(digest(p)==row['sha256'],'Session bright image hash differs')
            require(all(row['controls_readback'][k]==start['original_controls'][k] for k in ['ae','exp']),
                    'Bright original exposure differs')
            files[p.relative_to(root).as_posix()]=row['sha256']
    batches=[scan['preflight_capture']]+[p['capture'] for p in scan['points']]+[scan['return_origin_capture']['capture']]
    for index,batch in enumerate(batches,1):
        record=load(root/('preflight' if index==1 else 'return_origin' if index==75 else 'point_%03d'%(index-2))/'images/capture.json')
        require(batch['camera_session_id']==record['session_id']==start['session_id'] and record['sequence']==index,
                'Per-batch persistent sequence/session differs')
        require(record['session_start_sha256']==digest(folder/'session_start.json') and record['worker_sha256']==start['worker_sha256'],
                'Per-batch startup/worker binding differs')
        require(record['post_settle_flush']['discarded_frames']>=6,'Camera frames not refreshed after settling')
    return files
