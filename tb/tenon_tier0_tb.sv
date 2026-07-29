// SPDX-License-Identifier: Apache-2.0
// Functional checks for every Tier0 PadCell adapter.

// verilog_format: off
`timescale 1ns/1ps
`default_nettype none
// verilog_format: on

`ifndef TENON_TIER0_DUT
`define TENON_TIER0_DUT tenon_tier0_padframe
`endif

module tenon_tier0_case #(
    parameter integer GPIO_COUNT    = 16,
    parameter integer PADS_PER_RAIL = 2
) (
    output reg done
);
  wire                  mgmt_clk_pad;
  wire                  mgmt_rst_n_pad;
  wire                  jtag_tck_pad;
  wire                  jtag_tms_pad;
  wire                  jtag_tdi_pad;
  wire                  jtag_tdo_pad;
  wire                  uart_rx_pad;
  wire                  uart_tx_pad;
  wire [GPIO_COUNT-1:0] gpio_pad;

  reg                   mgmt_clk_drive;
  reg                   mgmt_rst_n_drive;
  reg                   jtag_tck_drive;
  reg                   jtag_tms_drive;
  reg                   jtag_tdi_drive;
  reg                   uart_rx_drive;
  reg                   jtag_tdo_o;
  reg                   uart_tx_o;
  reg  [GPIO_COUNT-1:0] gpio_o;
  reg  [GPIO_COUNT-1:0] gpio_oe;
  reg  [GPIO_COUNT-1:0] external_gpio;
  reg  [GPIO_COUNT-1:0] external_gpio_oe;

  wire                  mgmt_clk_i;
  wire                  mgmt_rst_ni;
  wire                  jtag_tck_i;
  wire                  jtag_tms_i;
  wire                  jtag_tdi_i;
  wire                  uart_rx_i;
  wire [GPIO_COUNT-1:0] gpio_i;

  assign mgmt_clk_pad   = mgmt_clk_drive;
  assign mgmt_rst_n_pad = mgmt_rst_n_drive;
  assign jtag_tck_pad   = jtag_tck_drive;
  assign jtag_tms_pad   = jtag_tms_drive;
  assign jtag_tdi_pad   = jtag_tdi_drive;
  assign uart_rx_pad    = uart_rx_drive;
  assign gpio_pad       = external_gpio_oe ? external_gpio : {GPIO_COUNT{1'bz}};

  `TENON_TIER0_DUT #(
      .GPIO_COUNT   (GPIO_COUNT),
      .PADS_PER_RAIL(PADS_PER_RAIL)
  ) dut (
      .mgmt_clk_pad  (mgmt_clk_pad),
      .mgmt_rst_n_pad(mgmt_rst_n_pad),
      .jtag_tck_pad  (jtag_tck_pad),
      .jtag_tms_pad  (jtag_tms_pad),
      .jtag_tdi_pad  (jtag_tdi_pad),
      .jtag_tdo_pad  (jtag_tdo_pad),
      .uart_rx_pad   (uart_rx_pad),
      .uart_tx_pad   (uart_tx_pad),
      .gpio_pad      (gpio_pad),
      .mgmt_clk_i    (mgmt_clk_i),
      .mgmt_rst_ni   (mgmt_rst_ni),
      .jtag_tck_i    (jtag_tck_i),
      .jtag_tms_i    (jtag_tms_i),
      .jtag_tdi_i    (jtag_tdi_i),
      .jtag_tdo_o    (jtag_tdo_o),
      .uart_rx_i     (uart_rx_i),
      .uart_tx_o     (uart_tx_o),
      .gpio_i        (gpio_i),
      .gpio_o        (gpio_o),
      .gpio_oe       (gpio_oe)
  );

  initial begin
    done             = 1'b0;
    mgmt_clk_drive   = 1'b0;
    mgmt_rst_n_drive = 1'b1;
    jtag_tck_drive   = 1'b1;
    jtag_tms_drive   = 1'b0;
    jtag_tdi_drive   = 1'b1;
    uart_rx_drive    = 1'b0;
    jtag_tdo_o       = 1'b0;
    uart_tx_o        = 1'b0;
    gpio_o           = {GPIO_COUNT{1'b0}};
    gpio_oe          = {GPIO_COUNT{1'b0}};
    external_gpio    = {GPIO_COUNT{1'b0}};
    external_gpio_oe = {GPIO_COUNT{1'b0}};
    #1;

    if ({mgmt_clk_i, mgmt_rst_ni, jtag_tck_i, jtag_tms_i, jtag_tdi_i, uart_rx_i} !== 6'b011010) begin
      $fatal(1, "management input propagation failed for GPIO_COUNT=%0d", GPIO_COUNT);
    end
    if (gpio_pad[0] !== 1'bz) begin
      $fatal(1, "GPIO must be high-impedance while output-enable is low");
    end

    gpio_o[0]  = 1'b1;
    gpio_oe[0] = 1'b1;
    jtag_tdo_o = 1'b1;
    uart_tx_o  = 1'b1;
    #1;
    if (gpio_pad[0] !== 1'b1 || gpio_i[0] !== 1'b1) begin
      $fatal(1, "core-to-pad GPIO propagation failed");
    end
    if (jtag_tdo_pad !== 1'b1 || uart_tx_pad !== 1'b1) begin
      $fatal(1, "management output propagation failed");
    end

    gpio_oe[0]          = 1'b0;
    external_gpio[0]    = 1'b1;
    external_gpio_oe[0] = 1'b1;
    #1;
    if (gpio_i[0] !== 1'b1) begin
      $fatal(1, "pad-to-core GPIO propagation failed");
    end

    done = 1'b1;
  end
endmodule

module tenon_tier0_tb;
  wire done32;
  wire done64;
  wire done88;
  wire done128;

  tenon_tier0_case #(
      .GPIO_COUNT   (16),
      .PADS_PER_RAIL(2)
  ) qfn32 (
      .done(done32)
  );
  tenon_tier0_case #(
      .GPIO_COUNT   (40),
      .PADS_PER_RAIL(4)
  ) qfn64 (
      .done(done64)
  );
  tenon_tier0_case #(
      .GPIO_COUNT   (56),
      .PADS_PER_RAIL(6)
  ) qfn88 (
      .done(done88)
  );
  tenon_tier0_case #(
      .GPIO_COUNT   (88),
      .PADS_PER_RAIL(8)
  ) qfn128 (
      .done(done128)
  );

  initial begin
    wait (done32 && done64 && done88 && done128);
    $display("PASS: Tier0 padframe functional checks completed for all package variants.");
    $finish;
  end
endmodule

`default_nettype wire
