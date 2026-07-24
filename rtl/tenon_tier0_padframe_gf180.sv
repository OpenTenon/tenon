// SPDX-License-Identifier: Apache-2.0
// GF180MCU Tier0 adapter using the GF180 OCD IO PadCells.

`default_nettype none

module tenon_gf180_input_s (
    inout  wire pad,
    output wire pad_to_core,
    inout  wire dvdd,
    inout  wire dvss,
    inout  wire vdd,
    inout  wire vss
);
  (* keep = "true" *) gf180mcu_ocd_io__in_s u_pad (
      .PAD (pad),
      .Y   (pad_to_core),
      .PU  (1'b0),
      .PD  (1'b0),
      .DVDD(dvdd),
      .DVSS(dvss),
      .VDD (vdd),
      .VSS (vss)
  );
endmodule

module tenon_gf180_input_c (
    inout  wire pad,
    output wire pad_to_core,
    inout  wire dvdd,
    inout  wire dvss,
    inout  wire vdd,
    inout  wire vss
);
  (* keep = "true" *) gf180mcu_ocd_io__in_c u_pad (
      .PAD (pad),
      .Y   (pad_to_core),
      .PU  (1'b0),
      .PD  (1'b0),
      .DVDD(dvdd),
      .DVSS(dvss),
      .VDD (vdd),
      .VSS (vss)
  );
endmodule

module tenon_gf180_gpio (
    inout  wire pad,
    output wire pad_to_core,
    input  wire core_to_pad,
    input  wire core_oe,
    inout  wire dvdd,
    inout  wire dvss,
    inout  wire vdd,
    inout  wire vss
);
  (* keep = "true" *) gf180mcu_ocd_io__bi_24t u_pad (
      .PAD (pad),
      .A   (core_to_pad),
      .OE  (core_oe),
      .Y   (pad_to_core),
      .CS  (1'b0),
      .SL  (1'b0),
      .IE  (1'b1),
      .PU  (1'b0),
      .PD  (1'b0),
      .DVDD(dvdd),
      .DVSS(dvss),
      .VDD (vdd),
      .VSS (vss)
  );
endmodule

module tenon_tier0_padframe_gf180 #(
    parameter integer GPIO_COUNT    = 16,
    parameter integer PADS_PER_RAIL = 2
) (
    inout  wire                  mgmt_clk_pad,
    inout  wire                  mgmt_rst_n_pad,
    inout  wire                  jtag_tck_pad,
    inout  wire                  jtag_tms_pad,
    inout  wire                  jtag_tdi_pad,
    inout  wire                  jtag_tdo_pad,
    inout  wire                  uart_rx_pad,
    inout  wire                  uart_tx_pad,
    inout  wire [GPIO_COUNT-1:0] gpio_pad,
    output wire                  mgmt_clk_i,
    output wire                  mgmt_rst_ni,
    output wire                  jtag_tck_i,
    output wire                  jtag_tms_i,
    output wire                  jtag_tdi_i,
    input  wire                  jtag_tdo_o,
    output wire                  uart_rx_i,
    input  wire                  uart_tx_o,
    output wire [GPIO_COUNT-1:0] gpio_i,
    input  wire [GPIO_COUNT-1:0] gpio_o,
    input  wire [GPIO_COUNT-1:0] gpio_oe
);
  // GF180 OCD identifies the core supply as DVDD/DVSS and the IO supply as VDD/VSS.
  wire dvdd;
  wire dvss;
  wire vdd;
  wire vss;
  genvar index;

  generate
    for (index = 0; index < PADS_PER_RAIL; index = index + 1) begin : u_iovdd_pads
      (* keep = "true" *) gf180mcu_ocd_io__vdd u_pad (
          .DVDD(dvdd),
          .DVSS(dvss),
          .VDD (vdd),
          .VSS (vss)
      );
    end
    for (index = 0; index < PADS_PER_RAIL; index = index + 1) begin : u_iovss_pads
      (* keep = "true" *) gf180mcu_ocd_io__vss u_pad (
          .DVDD(dvdd),
          .DVSS(dvss),
          .VDD (vdd),
          .VSS (vss)
      );
    end
    for (index = 0; index < PADS_PER_RAIL; index = index + 1) begin : u_vdd_pads
      (* keep = "true" *) gf180mcu_ocd_io__dvdd u_pad (
          .DVDD(dvdd),
          .DVSS(dvss),
          .VDD (vdd),
          .VSS (vss)
      );
    end
    for (index = 0; index < PADS_PER_RAIL; index = index + 1) begin : u_vss_pads
      (* keep = "true" *) gf180mcu_ocd_io__dvss u_pad (
          .DVDD(dvdd),
          .DVSS(dvss),
          .VDD (vdd),
          .VSS (vss)
      );
    end
  endgenerate

  tenon_gf180_input_s u_mgmt_clk_pad (
      .pad        (mgmt_clk_pad),
      .pad_to_core(mgmt_clk_i),
      .dvdd       (dvdd),
      .dvss       (dvss),
      .vdd        (vdd),
      .vss        (vss)
  );
  tenon_gf180_input_c u_mgmt_rst_n_pad (
      .pad        (mgmt_rst_n_pad),
      .pad_to_core(mgmt_rst_ni),
      .dvdd       (dvdd),
      .dvss       (dvss),
      .vdd        (vdd),
      .vss        (vss)
  );
  tenon_gf180_input_s u_jtag_tck_pad (
      .pad        (jtag_tck_pad),
      .pad_to_core(jtag_tck_i),
      .dvdd       (dvdd),
      .dvss       (dvss),
      .vdd        (vdd),
      .vss        (vss)
  );
  tenon_gf180_input_c u_jtag_tms_pad (
      .pad        (jtag_tms_pad),
      .pad_to_core(jtag_tms_i),
      .dvdd       (dvdd),
      .dvss       (dvss),
      .vdd        (vdd),
      .vss        (vss)
  );
  tenon_gf180_input_c u_jtag_tdi_pad (
      .pad        (jtag_tdi_pad),
      .pad_to_core(jtag_tdi_i),
      .dvdd       (dvdd),
      .dvss       (dvss),
      .vdd        (vdd),
      .vss        (vss)
  );
  tenon_gf180_gpio u_jtag_tdo_pad (
      .pad        (jtag_tdo_pad),
      .pad_to_core(),
      .core_to_pad(jtag_tdo_o),
      .core_oe    (1'b1),
      .dvdd       (dvdd),
      .dvss       (dvss),
      .vdd        (vdd),
      .vss        (vss)
  );
  tenon_gf180_input_c u_uart_rx_pad (
      .pad        (uart_rx_pad),
      .pad_to_core(uart_rx_i),
      .dvdd       (dvdd),
      .dvss       (dvss),
      .vdd        (vdd),
      .vss        (vss)
  );
  tenon_gf180_gpio u_uart_tx_pad (
      .pad        (uart_tx_pad),
      .pad_to_core(),
      .core_to_pad(uart_tx_o),
      .core_oe    (1'b1),
      .dvdd       (dvdd),
      .dvss       (dvss),
      .vdd        (vdd),
      .vss        (vss)
  );

  generate
    for (index = 0; index < GPIO_COUNT; index = index + 1) begin : u_gpio_pads
      tenon_gf180_gpio u_pad (
          .pad        (gpio_pad[index]),
          .pad_to_core(gpio_i[index]),
          .core_to_pad(gpio_o[index]),
          .core_oe    (gpio_oe[index]),
          .dvdd       (dvdd),
          .dvss       (dvss),
          .vdd        (vdd),
          .vss        (vss)
      );
    end
  endgenerate
endmodule

`default_nettype wire
