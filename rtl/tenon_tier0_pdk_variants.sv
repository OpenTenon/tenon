// SPDX-License-Identifier: Apache-2.0
// Fixed profile tops for PDK-specific Tier0 reference hardening.

`default_nettype none

// verilog_format: off
`define TENON_PDK_VARIANT(TOP, REFERENCE, WIDTH, RAIL_COUNT) \
module TOP ( \
    inout wire mgmt_clk_pad, \
    inout wire mgmt_rst_n_pad, \
    inout wire jtag_tck_pad, \
    inout wire jtag_tms_pad, \
    inout wire jtag_tdi_pad, \
    inout wire jtag_tdo_pad, \
    inout wire uart_rx_pad, \
    inout wire uart_tx_pad, \
    inout wire [(WIDTH)-1:0] gpio_pad \
); \
  REFERENCE #( \
      .GPIO_COUNT(WIDTH), \
      .PADS_PER_RAIL(RAIL_COUNT) \
  ) u_reference ( \
      .* \
  ); \
endmodule
// verilog_format: on

`TENON_PDK_VARIANT(tenon_tier0_sky130_qfn32, tenon_tier0_reference_sky130, 16, 2)
`TENON_PDK_VARIANT(tenon_tier0_sky130_qfn64, tenon_tier0_reference_sky130, 40, 4)
`TENON_PDK_VARIANT(tenon_tier0_sky130_qfn88, tenon_tier0_reference_sky130, 56, 6)
`TENON_PDK_VARIANT(tenon_tier0_sky130_qfn128, tenon_tier0_reference_sky130, 88, 8)

`TENON_PDK_VARIANT(tenon_tier0_gf180_qfn32, tenon_tier0_reference_gf180, 16, 2)
`TENON_PDK_VARIANT(tenon_tier0_gf180_qfn64, tenon_tier0_reference_gf180, 40, 4)
`TENON_PDK_VARIANT(tenon_tier0_gf180_qfn88, tenon_tier0_reference_gf180, 56, 6)
`TENON_PDK_VARIANT(tenon_tier0_gf180_qfn128, tenon_tier0_reference_gf180, 88, 8)

`undef TENON_PDK_VARIANT

`default_nettype wire
