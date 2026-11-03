# Independent simulation project; the board BD project is built by build_static_overlay.tcl.
source [file join [file dirname [info script]] project_paths.tcl]
set root $project_root
set project_name laser_pl_sim
set project_dir [file join $root vivado]
set header [open [file join $root tb project_paths.svh] w]
puts $header "`ifndef LASER_PROJECT_ROOT"
puts $header "`define LASER_PROJECT_ROOT \"$root\""
puts $header "`endif"
puts $header "`ifndef LASER_BASELINE_ROOT"
puts $header "`define LASER_BASELINE_ROOT \"$baseline_root\""
puts $header "`endif"
close $header
if {[file exists [file join $project_dir ${project_name}.xpr]]} {
    open_project [file join $project_dir ${project_name}.xpr]
} else {
    create_project $project_name $project_dir -part xc7z020clg400-1
}
set_property target_language Verilog [current_project]
set_property simulator_language Mixed [current_project]
set_property target_simulator XSim [current_project]
proc add_missing {fileset paths} {
    set missing {}
    foreach path $paths {if {[llength [get_files -quiet $path]] == 0} {lappend missing $path}}
    if {[llength $missing] > 0} {add_files -norecurse -fileset $fileset $missing}
}
add_missing sources_1 [glob -nocomplain [file join $root rtl *.sv]]
add_missing constrs_1 [list [file join $root constraints core_clock.xdc]]
add_missing sim_1 [glob -nocomplain [file join $root tb *.sv]]
set_property include_dirs [list [file join $root tb]] [get_filesets sim_1]
set vectors [concat [glob -nocomplain [file join $baseline_root vectors *_input.hex]] [glob -nocomplain [file join $baseline_root vectors *_input.bin]]]
add_missing sim_1 $vectors
foreach path $vectors {
    set item [get_files $path]
    if {[file extension $path] eq ".hex"} {set_property file_type {Memory Initialization Files} $item} else {set_property file_type {Data Files} $item}
    set_property used_in {simulation} $item
}
set refs [concat [glob -nocomplain [file join $baseline_root *.json]] [glob -nocomplain [file join $baseline_root *.md]] [glob -nocomplain [file join $baseline_root expected *.hex]] [glob -nocomplain [file join $baseline_root reference *.py]] [glob -nocomplain [file join $root *.md]]]
add_missing utils_1 $refs
set_property top laser_stripe_axi [get_filesets sources_1]
set_property top tb_laser_axi [get_filesets sim_1]
set_property xsim.simulate.runtime all [get_filesets sim_1]
update_compile_order -fileset sources_1
update_compile_order -fileset sim_1
puts "STRIPE_PROJECT_UPDATED project=[file join $project_dir ${project_name}.xpr] baseline=$baseline_root"
close_project
exit
