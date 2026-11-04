# Standalone core or AXI wrapper. The PS/DMA block design supplies its own clocks.
set standalone_clk [get_ports -quiet {clk aclk}]
if {[llength $standalone_clk] == 1} {
    create_clock -name core_clk -period 20.000 $standalone_clk
}
