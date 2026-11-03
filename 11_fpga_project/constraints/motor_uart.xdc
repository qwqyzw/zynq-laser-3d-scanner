# PYNQ-Z2 Arduino pins, verified against pinned official Xilinx/PYNQ base.xdc:
# e054c09c52db3244ef35c8ae08a2e62c1e4955fd, lines 64-65.
# Preserved source/provenance: ../third_party/pynq_z2/base_official.xdc.
set_property -dict {PACKAGE_PIN T14 IOSTANDARD LVCMOS33} [get_ports motor_uart_rx]
set_property -dict {PACKAGE_PIN U12 IOSTANDARD LVCMOS33} [get_ports motor_uart_tx]
set_property PULLUP true [get_ports motor_uart_rx]
# UART is asynchronous to FCLK0; the IP synchronizes RX internally. Only the
# asynchronous board boundary is excluded. All internal 50 MHz paths are timed.
set_false_path -from [get_ports motor_uart_rx]
set_false_path -to [get_ports motor_uart_tx]
