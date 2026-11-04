"""Compare already measured endpoint XYZ. No fitting, alignment or invented points."""
import csv,math

def compare_endpoints(first,last,*,minimum_common=20,max_p95_mm=3.0,max_p95_pixel_delta=2.0):
    common=sorted(set(first)&set(last))
    rows=[]
    for x in common:
        a,b=first[x],last[x]
        if not all(math.isfinite(float(v)) for v in (*a,*b)):
            raise ValueError('Nonfinite measured endpoint coordinate')
        rows.append({'x_px':x,'table_xyz_distance_mm':math.sqrt(sum((a[i]-b[i])**2 for i in range(3))),
                     'laser_y_displacement_px':abs(a[3]-b[3])})
    def percentile(values,p):
        values=sorted(values)
        if not values:return None
        index=(len(values)-1)*p;low=int(index);high=min(low+1,len(values)-1)
        return values[low]+(values[high]-values[low])*(index-low)
    xyz=percentile([r['table_xyz_distance_mm'] for r in rows],.95)
    pixels=percentile([r['laser_y_displacement_px'] for r in rows],.95)
    passed=bool(len(common)>=minimum_common and xyz is not None and xyz<=max_p95_mm
                and pixels is not None and pixels<=max_p95_pixel_delta)
    return {'passed':passed,'common_measured_object_columns':len(common),
            'endpoint_distance_p95_mm':xyz,'endpoint_pixel_displacement_p95_px':pixels,
            'limits':{'minimum_common_object_columns':minimum_common,'distance_p95_max_mm':max_p95_mm,
                      'laser_y_displacement_p95_max_px':max_p95_pixel_delta},
            'coordinate_alignment_applied':False,'object_fit_performed':False,'metric_accuracy_accepted':False,
            'full_axis_validation_passed':False,'scope':'endpoint closure only; not independent full-angle axis validation',
            'all_common_column_differences':rows}

def check_closure(points_csv,scan):
    if scan['plan']['max_angle_deg']!=360 or len(scan['points'])!=73:
        raise ValueError('Full-turn export requires all73 poses through360degrees')
    origin=scan['global_origin_motor_deg'];points=scan['points']
    span=points[-1]['motor_position_before_capture_deg']-points[0]['motor_position_before_capture_deg']
    if abs(span-360)>.4 or abs(scan['return_origin_capture']['motor_position_after_capture_deg']-origin-360)>.4:
        raise ValueError('Full-turn accumulated feedback or completed physical orientation did not close')
    ends={0:{},72:{}}
    with open(points_csv,newline='',encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            index=int(row['point_index'])
            if index not in ends:continue
            x=float(row['x_px'])
            if not math.isfinite(x) or not x.is_integer() or int(x) in ends[index]:
                raise ValueError('Endpoint columns must be unique finite actual PL columns')
            ends[index][int(x)]=tuple(float(row[k]) for k in ('x_mm','y_mm','z_mm','y_px'))
    result=compare_endpoints(ends[0],ends[72]);result['feedback_span_deg']=span
    result['initial_object_columns']=len(ends[0]);result['closing_object_columns']=len(ends[72])
    result['completion_feedback_offset_deg']=scan['return_origin_capture']['motor_position_after_capture_deg']-origin
    return result
