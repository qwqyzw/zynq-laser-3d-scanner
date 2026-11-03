set root [file normalize [file join [file dirname [info script]] ..]]
open_checkpoint [file join $root reports laser_stripe_core_synth.dcp]
create_clock -name core_clk -period 20.000 [get_ports clk]
report_timing_summary -file [file join $root reports core_timing_synth.rpt]
check_timing -verbose -file [file join $root reports core_check_timing.rpt]
write_checkpoint -force [file join $root reports laser_stripe_core_synth.dcp]
puts "SYNTH_TIMING_50MHZ_REPORTED"
exit
