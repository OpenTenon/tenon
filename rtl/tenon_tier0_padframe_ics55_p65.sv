// SPDX-License-Identifier: Apache-2.0
// Commercial P65/ICSIOA adapter for ICS55 no-PLL Tier0 padframes.

`default_nettype none

module tenon_ics55_p65_pbmux (
    inout  wire vdd,
    inout  wire vss,
    inout  wire vddio,
    inout  wire vssio,
    inout  wire pad,
    input  wire core_oe,
    output wire pad_to_core,
    input  wire core_to_pad
);
  // DS1/DS0 = 1/0 selects the commercial 8 mA CMOS drive configuration.
  (* keep = "true" *)
  P65_1233_PBMUX u_pad (
      .C    (pad_to_core),
      .PAD  (pad),
      .IE   (~core_oe),
      .CS   (1'b1),
      .I    (core_to_pad),
      .OE   (core_oe),
      .OD   (1'b0),
      .PU   (1'b0),
      .PD   (1'b0),
      .DS0  (1'b0),
      .DS1  (1'b1),
      .VDD  (vdd),
      .VDDIO(vddio),
      .VSS  (vss),
      .VSSIO(vssio)
  );
endmodule

module tenon_ics55_p65_vdd3 (
    inout wire vdd
);
  (* keep = "true" *) P65_1233_VDD3 u_pad (.VDD(vdd));
endmodule

module tenon_ics55_p65_vss3 (
    inout wire vdd,
    inout wire vss
);
  (* keep = "true" *) P65_1233_VSS3 u_pad (
      .VDD(vdd),
      .VSS(vss)
  );
endmodule

module tenon_ics55_p65_vddio3 (
    inout wire vddio
);
  (* keep = "true" *) P65_1233_VDDIO3 u_pad (.VDDIO(vddio));
endmodule
module tenon_ics55_p65_vssio3 (
    inout wire vddio,
    inout wire vssio
);
  (* keep = "true" *) P65_1233_VSSIO3 u_pad (
      .VDDIO(vddio),
      .VSSIO(vssio)
  );
endmodule

module tenon_tier0_padframe_ics55_no_pll #(
    parameter integer GPIO_COUNT    = 16,
    parameter integer PADS_PER_RAIL = 2
) (
    inout  wire                  iovdd,
    inout  wire                  iovss,
    inout  wire                  vdd,
    inout  wire                  vss,
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
  genvar index;

  (* keep = "true" *)
  P65_1233_CORNER u_corner_sw (
      .VDD  (vdd),
      .VSS  (vss),
      .VDDIO(iovdd),
      .VSSIO(iovss)
  );
  (* keep = "true" *)
  P65_1233_CORNER u_corner_se (
      .VDD  (vdd),
      .VSS  (vss),
      .VDDIO(iovdd),
      .VSSIO(iovss)
  );
  (* keep = "true" *)
  P65_1233_CORNER u_corner_ne (
      .VDD  (vdd),
      .VSS  (vss),
      .VDDIO(iovdd),
      .VSSIO(iovss)
  );
  (* keep = "true" *)
  P65_1233_CORNER u_corner_nw (
      .VDD  (vdd),
      .VSS  (vss),
      .VDDIO(iovdd),
      .VSSIO(iovss)
  );

  generate
    if (GPIO_COUNT == 16 && PADS_PER_RAIL == 2) begin : u_p65_qfn32
      tenon_ics55_p65_qfn32_no_pll_fillers u_fillers (
          .vdd  (vdd),
          .vss  (vss),
          .vddio(iovdd),
          .vssio(iovss)
      );
    end else if (GPIO_COUNT == 40 && PADS_PER_RAIL == 4) begin : u_p65_qfn64
      tenon_ics55_p65_qfn64_no_pll_fillers u_fillers (
          .vdd  (vdd),
          .vss  (vss),
          .vddio(iovdd),
          .vssio(iovss)
      );
    end else if (GPIO_COUNT == 56 && PADS_PER_RAIL == 6) begin : u_p65_qfn88
      tenon_ics55_p65_qfn88_no_pll_fillers u_fillers (
          .vdd  (vdd),
          .vss  (vss),
          .vddio(iovdd),
          .vssio(iovss)
      );
    end else if (GPIO_COUNT == 88 && PADS_PER_RAIL == 8) begin : u_p65_qfn128
      tenon_ics55_p65_qfn128_no_pll_fillers u_fillers (
          .vdd  (vdd),
          .vss  (vss),
          .vddio(iovdd),
          .vssio(iovss)
      );
    end
  endgenerate

  generate
    for (index = 0; index < PADS_PER_RAIL; index = index + 1) begin : u_iovdd_pads
      tenon_ics55_p65_vddio3 u_cell (.vddio(iovdd));
    end
    for (index = 0; index < PADS_PER_RAIL; index = index + 1) begin : u_iovss_pads
      tenon_ics55_p65_vssio3 u_cell (
          .vddio(iovdd),
          .vssio(iovss)
      );
    end
    for (index = 0; index < PADS_PER_RAIL; index = index + 1) begin : u_vdd_pads
      tenon_ics55_p65_vdd3 u_cell (.vdd(vdd));
    end
    for (index = 0; index < PADS_PER_RAIL; index = index + 1) begin : u_vss_pads
      tenon_ics55_p65_vss3 u_cell (
          .vdd(vdd),
          .vss(vss)
      );
    end
  endgenerate

  tenon_ics55_p65_pbmux u_mgmt_clk_pad (
      .vdd        (vdd),
      .vss        (vss),
      .vddio      (iovdd),
      .vssio      (iovss),
      .pad        (mgmt_clk_pad),
      .core_oe    (1'b0),
      .pad_to_core(mgmt_clk_i),
      .core_to_pad(1'b0)
  );
  tenon_ics55_p65_pbmux u_mgmt_rst_n_pad (
      .vdd        (vdd),
      .vss        (vss),
      .vddio      (iovdd),
      .vssio      (iovss),
      .pad        (mgmt_rst_n_pad),
      .core_oe    (1'b0),
      .pad_to_core(mgmt_rst_ni),
      .core_to_pad(1'b0)
  );
  tenon_ics55_p65_pbmux u_jtag_tck_pad (
      .vdd        (vdd),
      .vss        (vss),
      .vddio      (iovdd),
      .vssio      (iovss),
      .pad        (jtag_tck_pad),
      .core_oe    (1'b0),
      .pad_to_core(jtag_tck_i),
      .core_to_pad(1'b0)
  );
  tenon_ics55_p65_pbmux u_jtag_tms_pad (
      .vdd        (vdd),
      .vss        (vss),
      .vddio      (iovdd),
      .vssio      (iovss),
      .pad        (jtag_tms_pad),
      .core_oe    (1'b0),
      .pad_to_core(jtag_tms_i),
      .core_to_pad(1'b0)
  );
  tenon_ics55_p65_pbmux u_jtag_tdi_pad (
      .vdd        (vdd),
      .vss        (vss),
      .vddio      (iovdd),
      .vssio      (iovss),
      .pad        (jtag_tdi_pad),
      .core_oe    (1'b0),
      .pad_to_core(jtag_tdi_i),
      .core_to_pad(1'b0)
  );
  tenon_ics55_p65_pbmux u_jtag_tdo_pad (
      .vdd        (vdd),
      .vss        (vss),
      .vddio      (iovdd),
      .vssio      (iovss),
      .pad        (jtag_tdo_pad),
      .core_oe    (1'b1),
      .pad_to_core(),
      .core_to_pad(jtag_tdo_o)
  );
  tenon_ics55_p65_pbmux u_uart_rx_pad (
      .vdd        (vdd),
      .vss        (vss),
      .vddio      (iovdd),
      .vssio      (iovss),
      .pad        (uart_rx_pad),
      .core_oe    (1'b0),
      .pad_to_core(uart_rx_i),
      .core_to_pad(1'b0)
  );
  tenon_ics55_p65_pbmux u_uart_tx_pad (
      .vdd        (vdd),
      .vss        (vss),
      .vddio      (iovdd),
      .vssio      (iovss),
      .pad        (uart_tx_pad),
      .core_oe    (1'b1),
      .pad_to_core(),
      .core_to_pad(uart_tx_o)
  );

  generate
    for (index = 0; index < GPIO_COUNT; index = index + 1) begin : u_gpio_pads
      tenon_ics55_p65_pbmux u_cell (
          .vdd        (vdd),
          .vss        (vss),
          .vddio      (iovdd),
          .vssio      (iovss),
          .pad        (gpio_pad[index]),
          .core_oe    (gpio_oe[index]),
          .pad_to_core(gpio_i[index]),
          .core_to_pad(gpio_o[index])
      );
    end
  endgenerate
endmodule

`default_nettype wire
