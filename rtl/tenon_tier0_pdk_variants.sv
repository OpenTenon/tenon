// SPDX-License-Identifier: Apache-2.0
// Fixed profile tops for PDK-specific Tier0 reference hardening.

`default_nettype none

// verilog_format: off
`define TENON_SKY130_VARIANT(TOP, WIDTH, RAIL_COUNT) \
module TOP ( \
    inout wire vccd, \
    inout wire vssd, \
    inout wire vddio, \
    inout wire vssio, \
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
  tenon_tier0_reference_sky130 #( \
      .GPIO_COUNT(WIDTH), \
      .PADS_PER_RAIL(RAIL_COUNT) \
  ) u_reference ( \
      .* \
  ); \
endmodule

`define TENON_GF180_VARIANT(TOP, WIDTH, RAIL_COUNT) \
module TOP ( \
    inout wire iovdd, \
    inout wire iovss, \
    inout wire vdd, \
    inout wire vss, \
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
  tenon_tier0_reference_gf180 #( \
      .GPIO_COUNT(WIDTH), \
      .PADS_PER_RAIL(RAIL_COUNT) \
  ) u_reference ( \
      .* \
  ); \
endmodule

`define TENON_ICS55_NO_PLL_VARIANT(TOP, WIDTH, RAIL_COUNT) \
module TOP ( \
    inout wire iovdd, \
    inout wire iovss, \
    inout wire vdd, \
    inout wire vss, \
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
  tenon_tier0_reference_ics55_no_pll #( \
      .GPIO_COUNT(WIDTH), \
      .PADS_PER_RAIL(RAIL_COUNT) \
  ) u_reference ( \
      .* \
  ); \
endmodule

`define TENON_ICS55_PLL_VARIANT(TOP, WIDTH, RAIL_COUNT) \
module TOP ( \
    inout wire iovdd, \
    inout wire iovss, \
    inout wire vdd, \
    inout wire vss, \
    inout wire pll_avdd, \
    inout wire pll_avss, \
    inout wire pll_avddio, \
    inout wire pll_avssio, \
    inout wire mgmt_clk_pad, \
    inout wire mgmt_rst_n_pad, \
    inout wire jtag_tck_pad, \
    inout wire jtag_tms_pad, \
    inout wire jtag_tdi_pad, \
    inout wire jtag_tdo_pad, \
    inout wire uart_rx_pad, \
    inout wire uart_tx_pad, \
    inout wire pll_xin_pad, \
    inout wire pll_xout_pad, \
    inout wire [(WIDTH)-1:0] gpio_pad \
); \
  tenon_tier0_reference_ics55_pll #( \
      .GPIO_COUNT(WIDTH), \
      .PADS_PER_RAIL(RAIL_COUNT) \
  ) u_reference ( \
      .* \
  ); \
endmodule
// verilog_format: on

`TENON_SKY130_VARIANT(tenon_tier0_sky130_qfn32, 16, 2)
`TENON_SKY130_VARIANT(tenon_tier0_sky130_qfn64, 40, 4)
`TENON_SKY130_VARIANT(tenon_tier0_sky130_qfn88, 56, 6)
`TENON_SKY130_VARIANT(tenon_tier0_sky130_qfn128, 88, 8)

`TENON_GF180_VARIANT(tenon_tier0_gf180_qfn32, 16, 2)
`TENON_GF180_VARIANT(tenon_tier0_gf180_qfn64, 40, 4)
`TENON_GF180_VARIANT(tenon_tier0_gf180_qfn88, 56, 6)
`TENON_GF180_VARIANT(tenon_tier0_gf180_qfn128, 88, 8)

`TENON_ICS55_NO_PLL_VARIANT(tenon_tier0_ics55_qfn32_no_pll, 16, 2)
`TENON_ICS55_NO_PLL_VARIANT(tenon_tier0_ics55_qfn64_no_pll, 40, 4)
`TENON_ICS55_NO_PLL_VARIANT(tenon_tier0_ics55_qfn88_no_pll, 56, 6)
`TENON_ICS55_NO_PLL_VARIANT(tenon_tier0_ics55_qfn128_no_pll, 88, 8)

`TENON_ICS55_PLL_VARIANT(tenon_tier0_ics55_qfn32_pll, 10, 2)
`TENON_ICS55_PLL_VARIANT(tenon_tier0_ics55_qfn64_pll, 34, 4)
`TENON_ICS55_PLL_VARIANT(tenon_tier0_ics55_qfn88_pll, 50, 6)
`TENON_ICS55_PLL_VARIANT(tenon_tier0_ics55_qfn128_pll, 82, 8)

`undef TENON_SKY130_VARIANT
`undef TENON_GF180_VARIANT
`undef TENON_ICS55_NO_PLL_VARIANT
`undef TENON_ICS55_PLL_VARIANT

`default_nettype wire
