import unittest
from pointcloud_review import assess
def row(i,x,y,z):return {'point_index':i,'x_mm':x,'y_mm':y,'z_mm':z,'height_above_reference_mm':z}
class Checks(unittest.TestCase):
    def test_closure_not_double_counted_in_angular_coverage(self):
        report,_,_=assess([row(i,i%3,i%5,3+i%7) for i in [0,1,2,3,4]],list(range(0,361,90)))
        self.assertEqual(report['nonempty_unique_angle_fraction'],1);self.assertEqual(report['unique_angular_samples'],4)
        self.assertEqual(report['surface_construction'],'deferred_pending_pointcloud_and_shape_review')
        self.assertFalse(report['surface_fitted'])
    def test_wrapping_missing_arc_and_sparse_profiles(self):
        report,_,_=assess([row(1,1,2,3)],list(range(0,361,90)))
        self.assertEqual(report['maximum_empty_arc_deg'],270);self.assertEqual(report['empty_unique_angles_deg'],[0,180,270])
        self.assertEqual(report['low_object_support_angles_deg'],[90])
    def test_invalid_noninteger_outside_or_nan_rejected(self):
        for r in [row(.5,0,0,5),row(5,0,0,5),row(0,float('nan'),0,5)]:
            with self.assertRaises(ValueError):assess([r],list(range(0,361,90)))
    def test_no_points_is_valid_report_not_fabricated_surface(self):
        r,_,_=assess([],list(range(0,361,90)));self.assertEqual(r['points'],0);self.assertEqual(r['maximum_empty_arc_deg'],360)
        self.assertNotIn('measured_height_mm',r)
if __name__=='__main__':unittest.main(verbosity=2)
