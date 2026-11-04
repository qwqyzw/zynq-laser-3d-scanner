# Official PYNQ-Z2 PS7 settings

Source: [Xilinx/PYNQ board-specific hardware Tcl](https://github.com/Xilinx/PYNQ/blob/e054c09c52db3244ef35c8ae08a2e62c1e4955fd/boards/Pynq-Z2/petalinux_bsp/hardware_project/pynqz2.tcl), repository commit `e054c09c52db3244ef35c8ae08a2e62c1e4955fd`.

`pynqz2_official.tcl` preserves the downloaded source. `ps7_settings.tcl` extracts the complete PS7 CONFIG list without changing values. The overlay applies this list and then explicitly changes only fabric clock/AXI settings. The source script was produced for Vivado2024.1; actual compatibility with Vivado2026.1 is checked by creating and validating the design.

The original script is marked copyright2022Xilinx and BSD-3-Clause. The repository LICENSE is preserved. `SHA256.json` records download and extracted file SHA256 digests.

Board-specific settings include50MHz PS crystal,525MHz16-bit DDR(`MT41J256M16 RE-125`,512MiB),UART0MIO14..15,Ethernet0MIO16..27 andUSB0MIO28..39/resetMIO46. This is the PYNQ-Z2 preset, not a ZedBoard configuration.

The path was removed in [upstream commit945c35f](https://github.com/Xilinx/PYNQ/commit/945c35f85c28242ff9876230d27c142c7742618d) when PYNQ4.0 dropped Zynq-7000 board packages. The preceding revision above deliberately preserves the board configuration for this existing PYNQ-Z2 target. Confirm the installed PYNQ3.x image/driver before board deployment.

`base_official.xdc` preserves the [official base overlay constraints at the same commit](https://github.com/Xilinx/PYNQ/blob/e054c09c52db3244ef35c8ae08a2e62c1e4955fd/boards/Pynq-Z2/base/vivado/constraints/base.xdc). Its Arduino D0/T14 and D1/U12 mapping is used by the separately versioned motor UART overlay. `base_xdc_provenance.json` records the source and SHA256. Only these two LVCMOS33 pins are selected; the whole base XDC is not applied to the smaller custom design.
