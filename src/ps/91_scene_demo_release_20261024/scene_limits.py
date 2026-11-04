"""Explicit bounded optical tolerance for demonstration, not accuracy approval."""
def limits():return {'maximum_shift_px':4.0,'minimum_correlation':.75,'minimum_peak_margin':.02,'old_maximum_shift_px':2.0,'old_minimum_correlation':.9,'all_four_references_required':True,'demonstration_only':True}
def evaluate_patch(correlation,shift,margin):
 import math
 finite=all(math.isfinite(float(x)) for x in [correlation,shift,margin])
 return {'passed':bool(finite and correlation>=.75 and 0<=shift<=4.0 and margin>=.02),'strict_passed':bool(finite and correlation>=.9 and 0<=shift<=2 and margin>=.02),'demonstration_only':True}
