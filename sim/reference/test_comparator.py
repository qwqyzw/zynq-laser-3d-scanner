"""Negative-control tests for the comparison tool, not for any DUT."""
import unittest
import numpy as np
from horizontal_vectors import compare,format_errors

class ComparatorChecks(unittest.TestCase):
    def setUp(self):self.zero=np.zeros(640,np.uint32)
    def test_old_480_record_interface_rejected(self):
        with self.assertRaises(ValueError):format_errors(np.zeros(480,np.uint32))
    def test_invalid_reserved_and_out_of_range_rejected(self):
        for word in [1,1<<18,0x80000000|(480*256),0x80040000]:
            a=self.zero.copy();a[10]=word
            self.assertFalse(compare(a,self.zero)[0]['passed'])
    def test_valid_mismatch_rejected(self):
        a=self.zero.copy();a[10]=0x8000c880
        self.assertEqual(compare(a,self.zero)[0]['valid_mismatch_columns'],[10])
    def test_coordinate_tolerance_boundary(self):
        exp=self.zero.copy();exp[10]=0x8000c880
        a=exp.copy();a[10]+=64;self.assertTrue(compare(a,exp,.25)[0]['passed'])
        a[10]+=1;self.assertFalse(compare(a,exp,.25)[0]['passed'])
    def test_real_zero_output_is_legal(self):
        self.assertTrue(compare(self.zero,self.zero)[0]['passed'])
if __name__=='__main__':unittest.main()
