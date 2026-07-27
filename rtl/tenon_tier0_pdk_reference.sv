// SPDX-License-Identifier: Apache-2.0
// Standalone reference tops for the Sky130A and GF180MCU Tier0 adapters.

`default_nettype none

module tenon_tier0_reference_sky130 #(
    parameter integer GPIO_COUNT    = 16,
    parameter integer PADS_PER_RAIL = 2
) (
    inout wire                  vccd,
    inout wire                  vssd,
    inout wire                  vddio,
    inout wire                  vssio,
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
      .vccd          (vccd),
      .vssd          (vssd),
      .vddio         (vddio),
      .vssio         (vssio),
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
    inout wire                  iovdd,
    inout wire                  iovss,
    inout wire                  vdd,
    inout wire                  vss,
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
      .iovdd         (iovdd),
      .iovss         (iovss),
      .vdd           (vdd),
      .vss           (vss),
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

module tenon_tier0_reference_ics55_no_pll #(
    parameter integer GPIO_COUNT    = 16,
    parameter integer PADS_PER_RAIL = 2
) (
    inout wire                  iovdd,
    inout wire                  iovss,
    inout wire                  vdd,
    inout wire                  vss,
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

  tenon_tier0_padframe_ics55_no_pll #(
      .GPIO_COUNT   (GPIO_COUNT),
      .PADS_PER_RAIL(PADS_PER_RAIL)
  ) u_padframe (
      .iovdd         (iovdd),
      .iovss         (iovss),
      .vdd           (vdd),
      .vss           (vss),
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

module tenon_tier0_reference_ics55_pll #(
    parameter integer GPIO_COUNT    = 10,
    parameter integer PADS_PER_RAIL = 2
) (
    inout wire                  iovdd,
    inout wire                  iovss,
    inout wire                  vdd,
    inout wire                  vss,
    inout wire                  pll_avdd,
    inout wire                  pll_avss,
    inout wire                  pll_avddio,
    inout wire                  pll_avssio,
    inout wire                  mgmt_clk_pad,
    inout wire                  mgmt_rst_n_pad,
    inout wire                  jtag_tck_pad,
    inout wire                  jtag_tms_pad,
    inout wire                  jtag_tdi_pad,
    inout wire                  jtag_tdo_pad,
    inout wire                  uart_rx_pad,
    inout wire                  uart_tx_pad,
    inout wire                  pll_xin_pad,
    inout wire                  pll_xout_pad,
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
  wire                  pll_ckout1;
  wire                  pll_ckout2;
  wire                  pll_cktest;

  assign gpio_o     = {GPIO_COUNT{1'b0}};
  assign gpio_oe    = {GPIO_COUNT{1'b0}};
  assign jtag_tdo_o = 1'b0;
  assign uart_tx_o  = 1'b0;

  tenon_tier0_padframe_ics55_pll #(
      .GPIO_COUNT   (GPIO_COUNT),
      .PADS_PER_RAIL(PADS_PER_RAIL)
  ) u_padframe (
      .iovdd         (iovdd),
      .iovss         (iovss),
      .vdd           (vdd),
      .vss           (vss),
      .pll_avdd      (pll_avdd),
      .pll_avss      (pll_avss),
      .pll_avddio    (pll_avddio),
      .pll_avssio    (pll_avssio),
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
      .mgmt_rst_ni   (mgmt_rst_ni),
      .jtag_tck_i    (jtag_tck_i),
      .jtag_tms_i    (jtag_tms_i),
      .jtag_tdi_i    (jtag_tdi_i),
      .jtag_tdo_o    (jtag_tdo_o),
      .uart_rx_i     (uart_rx_i),
      .uart_tx_o     (uart_tx_o),
      .gpio_i        (gpio_i),
      .gpio_o        (gpio_o),
      .gpio_oe       (gpio_oe),
      .pll_osc_enable(1'b1),
      .pll_en        (1'b0),
      .pll_bp        (1'b0),
      .pll_select    (1'b0),
      .pll_n         (8'b0),
      .pll_od        (2'b0),
      .pll_ckout1    (pll_ckout1),
      .pll_ckout2    (pll_ckout2),
      .pll_cktest    (pll_cktest)
  );
endmodule

`default_nettype wire
