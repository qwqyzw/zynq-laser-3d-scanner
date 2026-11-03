set root [file normalize [file join [file dirname [info script]] ..]]
cd $root
file mkdir [file join $root reports]
set_param general.maxThreads 4
create_project -in_memory -part xc7z020clg400-1
read_verilog -sv [file join $root rtl laser_stripe_core.sv]
read_xdc [file join $root constraints core_clock.xdc]
synth_design -top laser_stripe_core -part xc7z020clg400-1 -mode out_of_context
report_utilization -file [file join $root reports core_utilization_synth.rpt]
report_timing_summary -file [file join $root reports core_timing_synth.rpt]
check_timing -verbose -file [file join $root reports core_check_timing.rpt]
write_checkpoint -force [file join $root reports laser_stripe_core_synth.dcp]
puts "STRIPE_CORE_SYNTHESIS_PASSED"
close_project
exit
