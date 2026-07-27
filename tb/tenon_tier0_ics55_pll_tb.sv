// SPDX-License-Identifier: Apache-2.0
// CI checks for the ICS55 PB4 padframe with the optional commercial PLL macro.

`timescale 1ns / 1ps
// Keep a directive boundary: Icarus 11 parses a following directive on this
// line as part of `timescale.
`default_nettype none

module tenon_tier0_ics55_pll_tb;
  wire       mgmt_clk_pad;
  wire       mgmt_rst_n_pad;
  wire       jtag_tck_pad;
  wire       jtag_tms_pad;
  wire       jtag_tdi_pad;
  wire       jtag_tdo_pad;
  wire       uart_rx_pad;
  wire       uart_tx_pad;
  wire       pll_xin_pad;
  wire       pll_xout_pad;
  wire [9:0] gpio_pad;
  wire       pll_ckout1;
  wire       pll_ckout2;
  wire       pll_cktest;
  reg        mgmt_clk_drive;
  reg        gpio_drive;
  reg        gpio_drive_enable;
  reg  [9:0] gpio_o;
  reg  [9:0] gpio_oe;
  wire [9:0] gpio_i;
  wire       mgmt_clk_i;

  assign mgmt_clk_pad   = mgmt_clk_drive;
  assign mgmt_rst_n_pad = 1'b1;
  assign jtag_tck_pad   = 1'b0;
  assign jtag_tms_pad   = 1'b0;
  assign jtag_tdi_pad   = 1'b0;
  assign uart_rx_pad    = 1'b0;
  assign pll_xin_pad    = 1'b0;
  assign gpio_pad[0]    = gpio_drive_enable ? gpio_drive : 1'bz;

  tenon_tier0_padframe_ics55_pll #(
      .GPIO_COUNT   (10),
      .PADS_PER_RAIL(2)
  ) dut (
      .mgmt_clk_pad  (mgmt_clk_pad),
      .mgmt_rst_n_pad(mgmt_rst_n_pad),
      .jtag_tck_pad  (jtag_tck_pad),
      .jtag_tms_pad  (jtag_tms_pad),
      .jtag_tdi_pad  (jtag_tdi_pad),
      .jtag_tdo_pad  (jtag_tdo_pad),
      .uart_rx_pad   (uart_rx_pad),
      .uart_tx_pad   (uart_tx_pad),
      .pll_xin_pad   (pll_xin_pad),
      .pll_xout_pad  (pll_xout_pad),
      .gpio_pad      (gpio_pad),
      .mgmt_clk_i    (mgmt_clk_i),
      .mgmt_rst_ni   (),
      .jtag_tck_i    (),
      .jtag_tms_i    (),
      .jtag_tdi_i    (),
      .jtag_tdo_o    (1'b0),
      .uart_rx_i     (),
      .uart_tx_o     (1'b0),
      .gpio_i        (gpio_i),
      .gpio_o        (gpio_o),
      .gpio_oe       (gpio_oe),
      .pll_osc_enable(1'b1),
      .pll_en        (1'b1),
      .pll_bp        (1'b0),
      .pll_select    (1'b0),
      .pll_n         (8'b0),
      .pll_od        (2'b0),
      .pll_ckout1    (pll_ckout1),
      .pll_ckout2    (pll_ckout2),
      .pll_cktest    (pll_cktest)
  );

  initial begin
    mgmt_clk_drive    = 1'b1;
    gpio_drive        = 1'b0;
    gpio_drive_enable = 1'b0;
    gpio_o            = '0;
    gpio_oe           = '0;
    #1;
    if (mgmt_clk_i !== 1'b1) $fatal(1, "PB4 management input propagation failed");
    gpio_o[0]  = 1'b1;
    gpio_oe[0] = 1'b1;
    #1;
    if (gpio_pad[0] !== 1'b1 || gpio_i[0] !== 1'b1) $fatal(1, "PB4 GPIO output failed");
    if (pll_ckout1 !== 1'b0 || pll_ckout2 !== 1'b0)
      $fatal(1, "PLL reference clock must remain low when XIN is low");
    $display("PASS: ICS55 PLL padframe functional checks completed.");
    $finish;
  end
endmodule

`default_nettype wire
