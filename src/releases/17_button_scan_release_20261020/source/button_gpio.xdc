# PYNQ-Z2 user buttons and LEDs, from the pinned official base_official.xdc.
# Copyright of source mapping: Xilinx 2022, BSD-3-Clause.
set_property -dict {PACKAGE_PIN D19 IOSTANDARD LVCMOS33} [get_ports {scan_buttons[0]}]
set_property -dict {PACKAGE_PIN D20 IOSTANDARD LVCMOS33} [get_ports {scan_buttons[1]}]
set_property -dict {PACKAGE_PIN L20 IOSTANDARD LVCMOS33} [get_ports {scan_buttons[2]}]
set_property -dict {PACKAGE_PIN L19 IOSTANDARD LVCMOS33} [get_ports {scan_buttons[3]}]
set_property -dict {PACKAGE_PIN R14 IOSTANDARD LVCMOS33} [get_ports {scan_leds[0]}]
set_property -dict {PACKAGE_PIN P14 IOSTANDARD LVCMOS33} [get_ports {scan_leds[1]}]
set_property -dict {PACKAGE_PIN N16 IOSTANDARD LVCMOS33} [get_ports {scan_leds[2]}]
set_property -dict {PACKAGE_PIN M14 IOSTANDARD LVCMOS33} [get_ports {scan_leds[3]}]
# Exclude only asynchronous button -> first synchronizer D, not the second
# synchronizer stage or any internal data/control paths.
set scan_meta [get_cells -hier -filter {NAME =~ *button_sync_0*sync_meta_reg*}]
if {[llength $scan_meta] != 4} {
    error "Expected exactly four first-stage button synchronizer FFs, got $scan_meta"
}
set_false_path -from [get_ports {scan_buttons[*]}] -to [get_pins -of_objects $scan_meta -filter {REF_PIN_NAME == D}]
# LEDs are asynchronous visual indicators, without an external clock receiver.
set_false_path -to [get_ports {scan_leds[*]}]
