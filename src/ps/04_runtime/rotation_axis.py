"""Camera-frame rotation axis from measured chessboard poses and motor angles.

Import is offline only. Metric scale inherits measured printed-board uncertainty.
An excluded angle checks the model; gates are engineering checks, not accuracy certification.
"""
import json
from pathlib import Path
import cv2
import numpy as np
from geometric_calibration import object_points, corners, camera_arrays


def rotation(vector):
    return cv2.Rodrigues(np.asarray(vector,dtype=float))[0]


def pose_candidates(image,camera):
    if list(image.shape[1::-1])!=camera['image_size']:
        raise ValueError('Camera image mode differs from calibration')
    obj=object_points(camera['board']);found=corners(image,camera['board'])
    k,d=camera_arrays(camera);result=[]
    for pixels in (found,found[::-1].copy()):
        ok,rv,tv=cv2.solvePnP(obj,pixels,k,d,flags=cv2.SOLVEPNP_ITERATIVE)
        if not ok:continue
        R=rotation(rv);t=tv.ravel();xyz=obj@R.T+t
        projected=cv2.projectPoints(obj,rv,tv,k,d)[0].reshape(-1,2)
        rms=float(np.sqrt(np.mean(np.sum((projected-pixels.reshape(-1,2))**2,axis=1))))
        if (xyz[:,2]<=0).any() or rms>1:
            continue
        result.append({'R':R.tolist(),'t_mm':t.tolist(),'reprojection_rms_px':rms,
                       'pixels':pixels.reshape(-1,2).tolist()})
    if not result:raise ValueError('No complete, front-facing pose below1px RMS')
    return result


def select_pose(candidates,reference_R,angle_deg):
    """Resolve the rectangular chessboard's 180-degree labeling ambiguity."""
    if reference_R is None:return candidates[0]
    def mismatch(p):
        vector=cv2.Rodrigues(np.asarray(p['R'])@reference_R.T)[0].ravel()
        return abs(np.degrees(np.linalg.norm(vector))-abs(angle_deg))
    chosen=min(candidates,key=mismatch)
    if mismatch(chosen)>3:
        raise ValueError('Optical board rotation differs from motor by >3deg; do not relabel silently')
    return chosen


def fit_axis(poses,angles_deg,board,fit_indices):
    obj=object_points(board).astype(float);local_centre=obj.mean(axis=0)
    R0=np.asarray(poses[0]['R']);t0=np.asarray(poses[0]['t_mm'])
    centres=[np.asarray(p['R'])@local_centre+np.asarray(p['t_mm']) for p in poses]
    vectors=[]
    for i in fit_indices:
        if abs(angles_deg[i])<1:continue
        v=cv2.Rodrigues(np.asarray(poses[i]['R'])@R0.T)[0].ravel()
        vectors.append(v/np.radians(angles_deg[i]))
    if len(vectors)<2 or np.ptp(np.asarray(angles_deg)[fit_indices])<30:
        raise ValueError('Need at least2 nonzero fitting angles spanning30deg')
    _,_,vt=np.linalg.svd(np.asarray(vectors),full_matrices=False)
    axis=vt[0]
    if axis@np.mean(vectors,axis=0)<0:axis=-axis
    normal0=R0[:,2]
    if abs(axis@normal0)<.5:raise ValueError('Axis nearly parallel to board plane')
    A=[];b=[]
    for i in fit_indices:
        Q=rotation(axis*np.radians(angles_deg[i]))
        A.append(np.eye(3)-Q);b.append(centres[i]-Q@centres[0])
    # Select the unique point on this infinite axis at the initial paper plane.
    A.append(normal0.reshape(1,3));b.append(np.array([normal0@centres[0]]))
    A=np.vstack(A);b=np.concatenate(b)
    centre,_,rank,s=np.linalg.lstsq(A,b,rcond=None)
    if rank!=3 or s[-1]<1e-4:raise ValueError('Axis centre is underdetermined')
    residuals=[]
    for i,p in enumerate(poses):
        Q=rotation(axis*np.radians(angles_deg[i]))
        observed=obj@np.asarray(p['R']).T+np.asarray(p['t_mm'])
        predicted=(obj@R0.T+t0-centre)@Q.T+centre
        error=np.linalg.norm(predicted-observed,axis=1)
        rv=cv2.Rodrigues(np.asarray(p['R'])@(Q@R0).T)[0].ravel()
        residuals.append({'index':i,'angle_from_motor_deg':float(angles_deg[i]),
                          'used_for_fit':i in fit_indices,
                          'corner_xyz_rms_mm':float(np.sqrt(np.mean(error**2))),
                          'corner_xyz_p95_mm':float(np.percentile(error,95)),
                          'orientation_error_deg':float(np.degrees(np.linalg.norm(rv)))})
    return {'axis_direction_camera':axis.tolist(),'axis_point_on_initial_paper_mm':centre.tolist(),
            'centre_solve_singular_values':s.tolist(),'centre_condition_number':float(s[0]/s[-1]),
            'board_centres_camera_mm':[x.tolist() for x in centres],
            'initial_board_R':R0.tolist(),'initial_board_t_mm':t0.tolist(),
            'residuals':residuals}


def analyse(root,scan,camera):
    if scan['status']!='completed' or scan['simulation']:
        raise ValueError('Need a completed real scan')
    if len(scan['points'])!=5:raise ValueError('This review expects exactly5 angles')
    poses=[];angles=[];frame_review=[];ref=None
    for point in scan['points']:
        angle=point['table_relative_deg_from_motor_feedback'];group=[]
        for index in range(3):
            path=Path(root)/('point_%02d_raw_%02d.png'%(point['index'],index))
            image=cv2.imread(str(path))
            if image is None:raise ValueError('Missing raw frame '+str(path))
            pose=select_pose(pose_candidates(image,camera),ref,angle)
            if ref is None:ref=np.asarray(pose['R'])
            group.append(pose)
        pixels=np.asarray([p['pixels'] for p in group])
        span=np.linalg.norm(pixels-pixels.mean(axis=0),axis=2).max()
        if span>.75:raise ValueError('Chessboard shifted within3-frame batch')
        frame_review.append({'point_index':point['index'],'rms_px':[p['reprojection_rms_px'] for p in group],
                             'max_corner_deviation_from_batch_mean_px':float(span)})
        # Use the middle frame by fixed rule, never select the best numerical fit.
        poses.append(group[1]);angles.append(angle)
    fit_indices=[0,1,3,4];holdout=2
    model=fit_axis(poses,angles,camera['board'],fit_indices)
    gates={'all_fit_xyz_rms_le_0_75mm':all(model['residuals'][i]['corner_xyz_rms_mm']<=.75 for i in fit_indices),
           'holdout_xyz_rms_le_0_75mm':model['residuals'][holdout]['corner_xyz_rms_mm']<=.75,
           'all_orientation_error_le_1deg':all(r['orientation_error_deg']<=1 for r in model['residuals']),
           'centre_condition_le_20':model['centre_condition_number']<=20}
    return {'kind':'rotation_axis_candidate','camera_model_sha256':camera['source_sha256'],
            'fit_point_indices':fit_indices,'excluded_check_point_index':holdout,
            'pose_frame_selection':'Middle frame of each3-frame batch, fixed before fitting',
            'board':camera['board'],'frame_review':frame_review,'poses':poses,
            **model,'gates':gates,'candidate_checks_passed':all(gates.values()),
            'independent_metric_accuracy_verified':False,'point_cloud_fusion_verified':False,
            'scope':'Current fixed camera/rig only; not automatic recovery after movement'}


if __name__=='__main__':
    import sys
    root=Path(sys.argv[1]);scan=json.loads((root/'axis_scan.json').read_text())
    camera=json.loads((root/'camera_reused.json').read_text())
    result=analyse(root,scan,camera)
    with (root/'rotation_axis_candidate.json').open('x') as f:
        json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False)
    print(json.dumps({k:result[k] for k in ['axis_direction_camera','axis_point_on_initial_paper_mm','residuals','gates','candidate_checks_passed']},indent=2))
