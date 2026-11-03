set root [file normalize [file join [file dirname [info script]] ..]]
set project [file join $root build static ls.xpr]
if {![file isfile $project]} {
    error "Create the project first: vivado -mode batch -source scripts/build_static_overlay.tcl -tclargs create"
}
open_project $project
foreach source {laser_stripe_core.sv laser_stripe_axi.sv} {
    set files [get_files -quiet */$source]
    if {[llength $files] != 1} {error "Missing unique RTL source: $source"}
}
