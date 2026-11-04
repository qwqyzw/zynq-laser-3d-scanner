# Source this file from another script; never depend on the shell's directory.
set project_root [file normalize [file join [file dirname [info script]] ..]]
set baseline_root [file normalize [file join $project_root .. 01_fpga_baseline]]
if {![file isfile [file join $baseline_root manifest.json]]} {
    set baseline_root [file join $project_root handoff]
}
if {![file isfile [file join $baseline_root manifest.json]]} {
    error "Frozen baseline missing; keep 11_fpga_project beside 01_fpga_baseline"
}
