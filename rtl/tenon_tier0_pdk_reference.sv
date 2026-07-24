// SPDX-License-Identifier: Apache-2.0
// Standalone reference tops for the Sky130A and GF180MCU Tier0 adapters.

`default_nettype none

module tenon_tier0_reference_sky130 #(
    parameter integer GPIO_COUNT    = 16,
    parameter integer PADS_PER_RAIL = 2
) (
    inout wire                  mgmt_clk_pad,
    inout wire                  mgmt_rst_n_pad,
    inout wire                  jtag_tck_pad,
    inout wire                  jtag_tms_pad,
    inout wire                  jtag_tdi_pad,
    inout wire                  jtag_tdo_pad,
    inout wire                  uart_rx_pad,
    inout wire                  uart_tx_pad,
    inout wire [GPIO_COUNT-1:0] gpio_pad
);
  wire                  mgmt_clk_i;
  wire                  mgmt_rst_ni;
  wire                  jtag_tck_i;
  wire                  jtag_tms_i;
  wire                  jtag_tdi_i;
  wire                  uart_rx_i;
  wire [GPIO_COUNT-1:0] gpio_i;
  wire [GPIO_COUNT-1:0] gpio_o;
  wire [GPIO_COUNT-1:0] gpio_oe;
  wire                  jtag_tdo_o;
  wire                  uart_tx_o;

  assign gpio_o     = {GPIO_COUNT{1'b0}};
  assign gpio_oe    = {GPIO_COUNT{1'b0}};
  assign jtag_tdo_o = 1'b0;
  assign uart_tx_o  = 1'b0;

  tenon_tier0_padframe_sky130 #(
      .GPIO_COUNT   (GPIO_COUNT),
      .PADS_PER_RAIL(PADS_PER_RAIL)
  ) u_padframe (
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
endmodule

module tenon_tier0_reference_gf180 #(
    parameter integer GPIO_COUNT    = 16,
    parameter integer PADS_PER_RAIL = 2
) (
    inout wire                  mgmt_clk_pad,
    inout wire                  mgmt_rst_n_pad,
    inout wire                  jtag_tck_pad,
    inout wire                  jtag_tms_pad,
    inout wire                  jtag_tdi_pad,
    inout wire                  jtag_tdo_pad,
    inout wire                  uart_rx_pad,
    inout wire                  uart_tx_pad,
    inout wire [GPIO_COUNT-1:0] gpio_pad
);
  wire                  mgmt_clk_i;
  wire                  mgmt_rst_ni;
  wire                  jtag_tck_i;
  wire                  jtag_tms_i;
  wire                  jtag_tdi_i;
  wire                  uart_rx_i;
  wire [GPIO_COUNT-1:0] gpio_i;
  wire [GPIO_COUNT-1:0] gpio_o;
  wire [GPIO_COUNT-1:0] gpio_oe;
  wire                  jtag_tdo_o;
  wire                  uart_tx_o;

  assign gpio_o     = {GPIO_COUNT{1'b0}};
  assign gpio_oe    = {GPIO_COUNT{1'b0}};
  assign jtag_tdo_o = 1'b0;
  assign uart_tx_o  = 1'b0;

  tenon_tier0_padframe_gf180 #(
      .GPIO_COUNT   (GPIO_COUNT),
      .PADS_PER_RAIL(PADS_PER_RAIL)
  ) u_padframe (
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
endmodule

`default_nettype wire
