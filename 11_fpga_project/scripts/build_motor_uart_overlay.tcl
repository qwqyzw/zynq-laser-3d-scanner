# Reproducible PYNQ-Z2 PS + AXI DMA + laser_stripe_axi + AXI UARTLite overlay.
# Usage: vivado -mode batch -source scripts/build_motor_uart_overlay.tcl -tclargs create|synth|impl|bit ?build_dir? ?output_dir?
# Existing designs are opened, never recreated or force-overwritten.
set project_root [file normalize [file join [file dirname [info script]] ..]]
set stage create
if {[llength $argv] > 0} {set stage [lindex $argv 0]}
if {$stage ni {create synth impl bit}} {error "Stage must be create, synth, impl or bit"}
set build_dir [file join $project_root build motor_uart]
if {[llength $argv] > 1} {set build_dir [file normalize [lindex $argv 1]]}
if {[llength $argv] > 3} {error "Too many arguments"}
set project_name ls
set design_name bd
set reports [file join $project_root reports motor_uart]
set output_dir [file join $project_root output motor_uart]
if {[llength $argv] > 2} {set output_dir [file normalize [lindex $argv 2]]}
set xpr [file join $build_dir ${project_name}.xpr]
set marker [file join $build_dir motor_uart_overlay_owned.txt]
set rtl_files [list [file join $project_root rtl laser_stripe_core.sv] [file join $project_root rtl laser_stripe_axi.sv]]
foreach path $rtl_files {if {![file isfile $path]} {error "Required RTL missing: $path"}}
set preset [file join $project_root third_party pynq_z2 ps7_settings.tcl]
if {![file isfile $preset]} {error "Missing pinned official PYNQ-Z2 preset: $preset"}
file mkdir $reports
# Child synthesis processes inherit normalized ASCII TEMP/TMP paths. Short
# generated project/BD names keep vendor IP paths below Windows MAX_PATH.
set task_temp [file join $project_root build temp]
file mkdir $task_temp
set env(TEMP) $task_temp
set env(TMP) $task_temp
set_param general.maxThreads 1

proc require_one {objects description} {
    if {[llength $objects] != 1} {error "Expected one $description, found: $objects"}
    return [lindex $objects 0]
}
proc run_complete {name} {
    set status [get_property STATUS [get_runs $name]]
    if {![string match "*Complete*" $status]} {error "$name failed or incomplete: $status"}
}
proc checkpoint_run {name} {
    set status [get_property STATUS [get_runs $name]]
    if {[get_property NEEDS_REFRESH [get_runs $name]]} {
        error "$name is stale after a source change. Use a fresh build_dir to preserve previous results."
    }
    if {![string match "*Complete*" $status]} {
        launch_runs $name -jobs 1
        wait_on_run $name
    }
    run_complete $name
}

if {[file exists $xpr]} {
    if {![file isfile $marker]} {error "Refusing unrelated existing project without ownership marker: $xpr"}
    open_project $xpr
    open_bd_design [require_one [get_files -quiet */${design_name}.bd] {owned block design}]
    update_compile_order -fileset sources_1
    set ref_ip [require_one [get_ips -filter {IPDEF =~ xilinx.com:module_ref:laser_stripe_axi:*}] {laser module reference IP}]
    if {[get_property PROGRESS [get_runs synth_1]] eq "0%"} {update_module_reference $ref_ip}
} else {
    if {[file exists $build_dir] && [llength [glob -nocomplain -directory $build_dir *]] > 0} {
        error "Refusing nonempty build_dir without expected project: $build_dir"
    }
    file mkdir $build_dir
    create_project $project_name $build_dir -part xc7z020clg400-1
    set_property target_language Verilog [current_project]
    set_property simulator_language Mixed [current_project]
    set_property default_lib xil_defaultlib [current_project]
    add_files -norecurse $rtl_files
    add_files -fileset constrs_1 -norecurse [file join $project_root constraints motor_uart.xdc]
    update_compile_order -fileset sources_1
    create_bd_design $design_name

    set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 processing_system7_0]
    source $preset
    # Values below are the motor_uart overlay's fabric settings; DDR/MIO remain from PYNQ-Z2.
    set_property -dict $pynq_z2_ps7_settings $ps
    set_property -dict [list CONFIG.PCW_USE_M_AXI_GP0 {1} CONFIG.PCW_USE_S_AXI_HP0 {1} \
        CONFIG.PCW_S_AXI_HP0_DATA_WIDTH {64} CONFIG.PCW_EN_CLK0_PORT {1} \
        CONFIG.PCW_EN_CLK1_PORT {0} CONFIG.PCW_EN_CLK2_PORT {0} CONFIG.PCW_EN_CLK3_PORT {0} \
        CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ {50.000000} CONFIG.PCW_USE_FABRIC_INTERRUPT {0}] $ps

    set ddr [create_bd_intf_port -mode Master -vlnv xilinx.com:interface:ddrx_rtl:1.0 DDR]
    set fixed [create_bd_intf_port -mode Master -vlnv xilinx.com:display_processing_system7:fixedio_rtl:1.0 FIXED_IO]
    connect_bd_intf_net $ddr [get_bd_intf_pins $ps/DDR]
    connect_bd_intf_net $fixed [get_bd_intf_pins $ps/FIXED_IO]

    set core [create_bd_cell -type module -reference laser_stripe_axi laser_stripe_0]
    set dma [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma:7.1 axi_dma_0]
    set_property -dict [list CONFIG.c_include_sg {0} CONFIG.c_include_mm2s {1} \
        CONFIG.c_include_s2mm {1} CONFIG.c_sg_length_width {23} \
        CONFIG.c_m_axi_mm2s_data_width {32} CONFIG.c_m_axi_s2mm_data_width {32} \
        CONFIG.c_m_axis_mm2s_tdata_width {32} CONFIG.c_s_axis_s2mm_tdata_width {32} \
        CONFIG.c_include_mm2s_dre {0} CONFIG.c_include_s2mm_dre {0} \
        CONFIG.c_mm2s_burst_size {16} CONFIG.c_s2mm_burst_size {16}] $dma
    set uart [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_uartlite:2.0 axi_uartlite_0]
    set_property -dict [list CONFIG.C_BAUDRATE {9600} CONFIG.C_DATA_BITS {8} \
        CONFIG.C_USE_PARITY {0} CONFIG.C_ODD_PARITY {0}] $uart
    set uart_rx [create_bd_port -dir I motor_uart_rx]
    set uart_tx [create_bd_port -dir O motor_uart_tx]
    connect_bd_net $uart_rx [get_bd_pins $uart/rx]
    connect_bd_net [get_bd_pins $uart/tx] $uart_tx
    set ctrl [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_control]
    set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {3}] $ctrl
    set mem [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_memory]
    set_property -dict [list CONFIG.NUM_SI {2} CONFIG.NUM_MI {1}] $mem
    set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_fclk0]
    # Reset polarity is propagated from FCLK_RESET0_N; C_EXT_RESET_HIGH is read-only.
    set const1 [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 clock_locked]
    set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $const1
    set const0 [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 reset_inactive]
    set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {0}] $const0

    connect_bd_intf_net [get_bd_intf_pins $ps/M_AXI_GP0] [get_bd_intf_pins $ctrl/S00_AXI]
    connect_bd_intf_net [get_bd_intf_pins $ctrl/M00_AXI] [get_bd_intf_pins $dma/S_AXI_LITE]
    connect_bd_intf_net [get_bd_intf_pins $ctrl/M01_AXI] [get_bd_intf_pins $core/S_AXI]
    connect_bd_intf_net [get_bd_intf_pins $ctrl/M02_AXI] [get_bd_intf_pins $uart/S_AXI]
    connect_bd_intf_net [get_bd_intf_pins $dma/M_AXI_MM2S] [get_bd_intf_pins $mem/S00_AXI]
    connect_bd_intf_net [get_bd_intf_pins $dma/M_AXI_S2MM] [get_bd_intf_pins $mem/S01_AXI]
    connect_bd_intf_net [get_bd_intf_pins $mem/M00_AXI] [get_bd_intf_pins $ps/S_AXI_HP0]
    connect_bd_intf_net [get_bd_intf_pins $dma/M_AXIS_MM2S] [get_bd_intf_pins $core/S_AXIS]
    connect_bd_intf_net [get_bd_intf_pins $core/M_AXIS] [get_bd_intf_pins $dma/S_AXIS_S2MM]

    set clocks [list $ps/M_AXI_GP0_ACLK $ps/S_AXI_HP0_ACLK $rst/slowest_sync_clk \
        $core/aclk $dma/s_axi_lite_aclk $dma/m_axi_mm2s_aclk $dma/m_axi_s2mm_aclk \
        $ctrl/ACLK $ctrl/S00_ACLK $ctrl/M00_ACLK $ctrl/M01_ACLK $ctrl/M02_ACLK $uart/s_axi_aclk \
        $mem/ACLK $mem/S00_ACLK $mem/S01_ACLK $mem/M00_ACLK]
    foreach pin $clocks {connect_bd_net [get_bd_pins $ps/FCLK_CLK0] [get_bd_pins $pin]}
    connect_bd_net [get_bd_pins $ps/FCLK_RESET0_N] [get_bd_pins $rst/ext_reset_in]
    connect_bd_net [get_bd_pins $const1/dout] [get_bd_pins $rst/dcm_locked] [get_bd_pins $rst/aux_reset_in]
    connect_bd_net [get_bd_pins $const0/dout] [get_bd_pins $rst/mb_debug_sys_rst]
    foreach pin [list $core/aresetn $dma/axi_resetn $uart/s_axi_aresetn] {
        connect_bd_net [get_bd_pins $rst/peripheral_aresetn] [get_bd_pins $pin]
    }
    foreach pin [list $ctrl/ARESETN $ctrl/S00_ARESETN $ctrl/M00_ARESETN $ctrl/M01_ARESETN $ctrl/M02_ARESETN \
        $mem/ARESETN $mem/S00_ARESETN $mem/S01_ARESETN $mem/M00_ARESETN] {
        connect_bd_net [get_bd_pins $rst/interconnect_aresetn] [get_bd_pins $pin]
    }
    # DMA is polled by software. Its interrupt outputs are intentionally unused.
    assign_bd_address -offset 0x40400000 -range 64K -target_address_space \
        [get_bd_addr_spaces $ps/Data] [get_bd_addr_segs $dma/S_AXI_LITE/Reg]
    set core_seg [require_one [get_bd_addr_segs -of_objects [get_bd_intf_pins $core/S_AXI]] {core slave address segment}]
    assign_bd_address -offset 0x43C00000 -range 64K -target_address_space [get_bd_addr_spaces $ps/Data] $core_seg
    assign_bd_address -offset 0x42C00000 -range 64K -target_address_space [get_bd_addr_spaces $ps/Data] [get_bd_addr_segs $uart/S_AXI/Reg]
    assign_bd_address
    save_bd_design
    set mark [open $marker w]
    puts $mark "Owned by scripts/build_motor_uart_overlay.tcl; board PYNQ-Z2; dedicated UARTLite 9600 8N1 D1 TX D0 RX"
    close $mark
}

# Normalize the generated project's inactive auxiliary reset if resuming the
# initial create-only build. proc_sys_reset's auxiliary input is active-low.
set aux_pin [get_bd_pins rst_fclk0/aux_reset_in]
set aux_net [get_bd_nets -of_objects $aux_pin]
set locked_net [get_bd_nets -of_objects [get_bd_pins clock_locked/dout]]
if {$aux_net ne $locked_net} {
    disconnect_bd_net $aux_net $aux_pin
    connect_bd_net [get_bd_pins clock_locked/dout] $aux_pin
}
validate_bd_design
if {[get_property CONFIG.C_EXT_RESET_HIGH [get_bd_cells rst_fclk0]] != 0 || \
    [get_property CONFIG.C_AUX_RESET_HIGH [get_bd_cells rst_fclk0]] != 0} {
    error "Expected active-low external and auxiliary reset on proc_sys_reset"
}
save_bd_design
set bd [require_one [get_files -quiet */${design_name}.bd] {block design file}]
set_property synth_checkpoint_mode None $bd
generate_target all $bd
set wrapper [make_wrapper -files $bd -top]
foreach path $wrapper {if {[llength [get_files -quiet $path]]==0} {add_files -norecurse $path}}
set_property top ${design_name}_wrapper [get_filesets sources_1]
update_compile_order -fileset sources_1
set synth_settings [file join $reports motor_uart_synth_settings.tcl]
set settings_text "set_param general.maxThreads 1\n"
set previous_settings ""
if {[file isfile $synth_settings]} {
    set settings_file [open $synth_settings r]
    set previous_settings [read $settings_file]
    close $settings_file
}
if {$previous_settings ne $settings_text} {
    set settings_file [open $synth_settings w]
    puts -nonewline $settings_file $settings_text
    close $settings_file
}
if {[llength [get_files -quiet $synth_settings]]==0} {add_files -fileset utils_1 -norecurse $synth_settings}
set_property STEPS.SYNTH_DESIGN.TCL.PRE $synth_settings [get_runs synth_1]
write_bd_tcl -force [file join $reports motor_uart_block_design.tcl]
set summary [open [file join $reports motor_uart_design_summary.txt] w]
puts $summary "Vivado [version -short]; stage=$stage; project=$xpr"
puts $summary "PYNQ-Z2 xc7z020clg400-1; FCLK0=50MHz; single clock domain"
puts $summary "PS preset pinned Xilinx/PYNQ e054c09c52db3244ef35c8ae08a2e62c1e4955fd"
puts $summary "AXI DMA simple, 32-bit streams, 23-bit BTT, no DRE; align buffers to4 bytes"
puts $summary "DMA base=0x40400000 range=64KiB; laser base=0x43C00000 range=64KiB"
puts $summary "UARTLite base=0x42C00000 range=64KiB; 9600 8N1; Arduino D1 TX U12; D0 RX T14; LVCMOS33"
foreach seg [get_bd_addr_segs] {
    puts $summary "$seg OFFSET=[get_property OFFSET $seg] RANGE=[get_property RANGE $seg]"
}
close $summary
if {$stage eq "create"} {
    puts "MOTOR_UART_OVERLAY_CREATE_PASS project=$xpr"
    close_project
    exit
}

checkpoint_run synth_1
open_run synth_1
report_utilization -hierarchical -file [file join $reports motor_uart_synth_utilization.rpt]
report_timing_summary -delay_type min_max -report_unconstrained -file [file join $reports motor_uart_synth_timing.rpt]
report_drc -file [file join $reports motor_uart_synth_drc.rpt]
if {$stage eq "synth"} {
    puts "MOTOR_UART_OVERLAY_SYNTH_PASS project=$xpr"
    close_project
    exit
}
close_design
checkpoint_run impl_1
open_run impl_1
report_utilization -file [file join $reports motor_uart_impl_utilization.rpt]
report_timing_summary -delay_type min_max -report_unconstrained -file [file join $reports motor_uart_impl_timing.rpt]
report_drc -file [file join $reports motor_uart_impl_drc.rpt]
check_timing -verbose -file [file join $reports motor_uart_impl_check_timing.rpt]
set worst [get_timing_paths -setup -max_paths 1]
if {[llength $worst]==0 || [get_property SLACK $worst]<0} {error "Implementation setup timing failed"}
set hold [get_timing_paths -hold -max_paths 1]
if {[llength $hold]==0 || [get_property SLACK $hold]<0} {error "Implementation hold timing failed"}
if {$stage eq "impl"} {
    puts "MOTOR_UART_OVERLAY_IMPL_PASS project=$xpr"
    close_project
    exit
}
set errors [get_drc_violations -quiet -filter {SEVERITY == Error}]
if {[llength $errors]>0} {error "Refusing bitstream export due to DRC errors: $errors"}
if {[file exists [file join $output_dir laser_motor_uart.bit]] || [file exists [file join $output_dir laser_motor_uart.hwh]]} {
    error "Refusing to overwrite previously exported bit/hwh in $output_dir"
}
file mkdir $output_dir
write_bitstream [file join $output_dir laser_motor_uart.bit]
set hwh [file join [get_property DIRECTORY [current_project]] ${project_name}.gen sources_1 bd $design_name hw_handoff ${design_name}.hwh]
if {![file isfile $hwh]} {error "Generated HWH missing: $hwh"}
file copy $hwh [file join $output_dir laser_motor_uart.hwh]
set manifest [open [file join $output_dir build_manifest.txt] w]
puts $manifest "Built together from $xpr using Vivado [version -short]; FCLK0 50MHz"
puts $manifest "Board PYNQ-Z2; source preset Xilinx/PYNQ e054c09c52db3244ef35c8ae08a2e62c1e4955fd"
puts $manifest "Laser core and dedicated axi_uartlite_0 coexist; UART @0x42C00000 9600 8N1; D1=U12 TX D0=T14 RX"
close $manifest
puts "MOTOR_UART_OVERLAY_BIT_PASS output=$output_dir"
close_project
exit
