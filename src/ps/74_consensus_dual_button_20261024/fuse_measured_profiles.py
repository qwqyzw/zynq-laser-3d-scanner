"""Merge two already-reviewed actual PL profiles, keeping measured coordinates.

This module computes no XYZ and fills no gaps. Geometry masks come from the
unchanged Zynq reconstructor. Cross-exposure conflicts are rejected, not fitted.
"""
import numpy as np

def _fuse_candidates(short,long,*,maximum_shared_y_difference=1.5,
                           minimum_long_chain=3,maximum_neighbour_y_jump=6.):
    def checked(data):
        p=np.asarray(data['pixels'],float);selected=np.asarray(data['selected'],bool)
        if p.ndim!=2 or p.shape[1]!=2 or selected.shape!=(len(p),):
            raise ValueError('Measured pixels and geometric mask differ')
        if not np.isfinite(p).all() or (len(p) and (np.any(p[:,0]!=np.rint(p[:,0])) or np.any(p[:,0]<0) or np.any(p[:,0]>=640) or np.any(p[:,1]<0) or np.any(p[:,1]>=480))):
            raise ValueError('Invalid measured coordinates')
        if len(np.unique(p[:,0]))!=len(p):raise ValueError('Duplicate columns within one exposure')
        if data.get('quality_gate_passed') is not True:return {},p
        return {int(p[i,0]):(i,float(p[i,1])) for i in np.flatnonzero(selected)},p
    if maximum_shared_y_difference!=1.5 or minimum_long_chain!=3 or maximum_neighbour_y_jump!=6.:
        raise ValueError('Explicit tested fusion policy required')
    s,sp=checked(short);l,lp=checked(long);candidates=[];conflicts=[]
    for x in sorted(set(s)|set(l)):
        if x in s and x in l:
            difference=abs(s[x][1]-l[x][1])
            if difference>maximum_shared_y_difference:
                conflicts.append({'x_px':x,'difference_px':difference});continue
            idx,y=s[x];exposure=5
        elif x in s:idx,y=s[x];exposure=5;difference=None
        else:idx,y=l[x];exposure=20;difference=None
        candidates.append({'x_px':x,'y_px':y,'source_exposure':exposure,'source_index':int(idx),'shared_y_difference_px':difference})
    # Newly recovered long-exposure samples need measured neighbour support.
    # Exact gaps and y discontinuities split chains; nothing is interpolated.
    supported=set();start=0
    for i in range(1,len(candidates)+1):
        split=i==len(candidates) or candidates[i]['x_px']!=candidates[i-1]['x_px']+1 or abs(candidates[i]['y_px']-candidates[i-1]['y_px'])>maximum_neighbour_y_jump
        if split:
            if i-start>=minimum_long_chain:supported.update(range(start,i))
            start=i
    accepted=[];unsupported=[]
    for i,row in enumerate(candidates):
        if row['source_exposure']==5 or i in supported:accepted.append(row)
        else:unsupported.append(row['x_px'])
    for row in accepted:
        original=sp if row['source_exposure']==5 else lp
        assert np.array_equal(original[row['source_index']],[row['x_px'],row['y_px']])
    return accepted,{'short_object_candidates':len(s),'long_object_candidates':len(l),
        'fused_points':len(accepted),'kept_short':sum(r['source_exposure']==5 for r in accepted),
        'added_long':sum(r['source_exposure']==20 for r in accepted),
        'shared_conflicts':conflicts,'unsupported_long_columns':unsupported,
        'maximum_shared_y_difference_px':1.5,'minimum_measured_chain':3,
        'maximum_measured_neighbour_y_jump_px':6.,'interpolated_points':0,
        'coordinates_are_exact_input_measurements':True}

def fuse_measured_profiles(short,long):
    """Do not let a weaker two-frame addition displace strict three-frame data."""
    strict=[]
    for part in [short,long]:
        original=dict(part);mask=np.asarray(part['selected'],bool).copy()
        added=set(part.get('temporal_columns',[]))
        mask&=np.array([int(x) not in added for x in np.asarray(part['pixels'])[:,0]],bool)
        original['selected']=mask;strict.append(original)
    baseline,base_report=_fuse_candidates(*strict)
    candidates,candidate_report=_fuse_candidates(short,long)
    baseline_x={r['x_px'] for r in baseline}
    extra=[r for r in candidates if r['x_px'] not in baseline_x]
    merged=sorted(baseline+extra,key=lambda r:r['x_px'])
    report=dict(candidate_report)
    report.update(fused_points=len(merged),strict_baseline_points=len(baseline),
        added_supported_temporal_points=len(extra),strict_baseline_preserved=True,
        strict_baseline_conflicts=base_report['shared_conflicts'],
        kept_short=sum(r['source_exposure']==5 for r in merged),
        added_long=sum(r['source_exposure']==20 for r in merged))
    return merged,report
