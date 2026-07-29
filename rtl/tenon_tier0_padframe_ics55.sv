// SPDX-License-Identifier: Apache-2.0
// ICS55 Tier0 adapters built from the commercial PB4 package-pad library.

`default_nettype none

module tenon_ics55_package_overlay #(
    parameter integer USE_PADI = 0
) ();
  generate
    if (USE_PADI) begin : u_physical
      (* keep = "true" *) PADI30 u_package_pad ();
    end else begin : u_physical
      (* keep = "true" *) PADO30 u_package_pad ();
    end
  endgenerate
endmodule

module tenon_ics55_pb4 #(
    parameter integer USE_PADI = 0
) (
    inout  wire vssd,
    inout  wire vss,
    inout  wire pad,
    input  wire core_oe,
    output wire pad_to_core,
    input  wire core_to_pad,
    inout  wire vdd25,
    inout  wire vdd,
    inout  wire fp,
    inout  wire fpb
);
  (* keep = "true" *) PB4 u_pad (
      .VSSD (vssd),
      .VSS  (vss),
      .PAD  (pad),
      .OEN  (~core_oe),
      .C    (pad_to_core),
      .IE   (1'b1),
      .I    (core_to_pad),
      .VDD25(vdd25),
      .VDD  (vdd),
      .FP   (fp),
      .FPB  (fpb)
  );
  tenon_ics55_package_overlay #(.USE_PADI(USE_PADI)) u_package_overlay ();
  (* keep = "true" *) PFILL10 u_filler ();
endmodule

module tenon_ics55_pvdd1 #(
    parameter integer USE_PADI = 0
) (
    inout wire VDD25,
    inout wire VSS,
    inout wire VDD,
    inout wire VSSD,
    inout wire FP,
    inout wire FPB
);
  (* keep = "true" *) PVDD1 u_pad (.*);
  tenon_ics55_package_overlay #(.USE_PADI(USE_PADI)) u_package_overlay ();
  (* keep = "true" *) PFILL10 u_filler ();
endmodule

module tenon_ics55_pvss1 #(
    parameter integer USE_PADI = 0
) (
    inout wire VSSD,
    inout wire VSS,
    inout wire VDD25,
    inout wire VDD,
    inout wire FPB,
    inout wire FP
);
  (* keep = "true" *) PVSS1 u_pad (.*);
  tenon_ics55_package_overlay #(.USE_PADI(USE_PADI)) u_package_overlay ();
  (* keep = "true" *) PFILL10 u_filler ();
endmodule

module tenon_ics55_pvdd2 #(
    parameter integer USE_PADI = 0
) (
    inout wire VDD25,
    inout wire VDD,
    inout wire FP,
    inout wire FPB,
    inout wire VSS,
    inout wire VSSD
);
  (* keep = "true" *) PVDD2 u_pad (.*);
  tenon_ics55_package_overlay #(.USE_PADI(USE_PADI)) u_package_overlay ();
  (* keep = "true" *) PFILL10 u_filler ();
endmodule

module tenon_ics55_pvss2 #(
    parameter integer USE_PADI = 0
) (
    inout wire VDD25,
    inout wire FP,
    inout wire FPB,
    inout wire VDD,
    inout wire VSSD,
    inout wire VSS
);
  (* keep = "true" *) PVSS2 u_pad (.*);
  tenon_ics55_package_overlay #(.USE_PADI(USE_PADI)) u_package_overlay ();
  (* keep = "true" *) PFILL10 u_filler ();
endmodule

module tenon_ics55_pvdd1cap #(
    parameter integer USE_PADI = 0
) (
    inout wire rail
);
  (* keep = "true" *) PVDD1CAP u_pad (.SVDD1CAP(rail));
  tenon_ics55_package_overlay #(.USE_PADI(USE_PADI)) u_package_overlay ();
  (* keep = "true" *) PFILL10 u_filler ();
endmodule

module tenon_ics55_pvss1cap #(
    parameter integer USE_PADI = 0
) (
    inout wire rail
);
  (* keep = "true" *) PVSS1CAP u_pad (.SVSS1CAP(rail));
  tenon_ics55_package_overlay #(.USE_PADI(USE_PADI)) u_package_overlay ();
  (* keep = "true" *) PFILL10 u_filler ();
endmodule

module tenon_ics55_pvdd3ap #(
    parameter integer USE_PADI = 0
) (
    inout wire rail
);
  (* keep = "true" *) PVDD3AP u_pad (.SAVDD(rail));
  tenon_ics55_package_overlay #(.USE_PADI(USE_PADI)) u_package_overlay ();
  (* keep = "true" *) PFILL10 u_filler ();
endmodule

module tenon_ics55_pvss3ap #(
    parameter integer USE_PADI = 0
) (
    inout wire rail
);
  (* keep = "true" *) PVSS3AP u_pad (.SAVSS(rail));
  tenon_ics55_package_overlay #(.USE_PADI(USE_PADI)) u_package_overlay ();
  (* keep = "true" *) PFILL10 u_filler ();
endmodule

module tenon_ics55_pxwe1 (
    inout  wire vssd,
    inout  wire vss,
    inout  wire vdd25,
    inout  wire vdd,
    inout  wire fp,
    inout  wire fpb,
    inout  wire xin_pad,
    inout  wire xout_pad,
    input  wire enable,
    output wire refclk
);
  (* keep = "true" *) PXWE1 u_pad (
      .VSSD (vssd),
      .VSS  (vss),
      .VDD25(vdd25),
      .VDD  (vdd),
      .FP   (fp),
      .FPB  (fpb),
      .XIN  (xin_pad),
      .XOUT (xout_pad),
      .E    (enable),
      .XC   (refclk)
  );
  (* keep = "true" *) PADO30 u_xin_overlay ();
  (* keep = "true" *) PADI30 u_xout_overlay ();
  (* keep = "true" *) PFILL10 u_filler ();
endmodule

module tenon_tier0_padframe_ics55_pb4_legacy #(
    parameter integer GPIO_COUNT    = 16,
    parameter integer PADS_PER_RAIL = 2
) (
    inout  wire                  iovdd,
    inout  wire                  iovss,
    inout  wire                  vdd,
    inout  wire                  vss,
    inout  wire                  fp,
    inout  wire                  fpb,
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

  (* keep = "true" *) PCORNER u_corner_sw ();
  (* keep = "true" *) PCORNER u_corner_se ();
  (* keep = "true" *) PCORNER u_corner_ne ();
  (* keep = "true" *) PCORNER u_corner_nw ();

  generate
    if (GPIO_COUNT == 16 && PADS_PER_RAIL == 2) begin : u_qfn32_no_pll_fillers
      tenon_ics55_qfn32_no_pll_fillers u_fillers ();
    end
    if (GPIO_COUNT == 40 && PADS_PER_RAIL == 4) begin : u_qfn64_no_pll_fillers
      tenon_ics55_qfn64_no_pll_fillers u_fillers ();
    end
    if (GPIO_COUNT == 56 && PADS_PER_RAIL == 6) begin : u_qfn88_no_pll_fillers
      tenon_ics55_qfn88_no_pll_fillers u_fillers ();
    end
    if (GPIO_COUNT == 88 && PADS_PER_RAIL == 8) begin : u_qfn128_no_pll_fillers
      tenon_ics55_qfn128_no_pll_fillers u_fillers ();
    end
  endgenerate

  generate
    for (index = 0; index < PADS_PER_RAIL; index = index + 1) begin : u_iovdd_pads
      tenon_ics55_pvdd2 #(
          .USE_PADI(index % 2)
      ) u_cell (
          .VDD25(iovdd),
          .VDD  (vdd),
          .FP   (fp),
          .FPB  (fpb),
          .VSS  (vss),
          .VSSD (iovss)
      );
    end
    for (index = 0; index < PADS_PER_RAIL; index = index + 1) begin : u_iovss_pads
      tenon_ics55_pvss2 #(
          .USE_PADI(index % 2)
      ) u_cell (
          .VDD25(iovdd),
          .FP   (fp),
          .FPB  (fpb),
          .VDD  (vdd),
          .VSSD (iovss),
          .VSS  (vss)
      );
    end
    for (index = 0; index < PADS_PER_RAIL; index = index + 1) begin : u_vdd_pads
      tenon_ics55_pvdd1 #(
          .USE_PADI(index % 2)
      ) u_cell (
          .VDD25(iovdd),
          .VSS  (vss),
          .VDD  (vdd),
          .VSSD (iovss),
          .FP   (fp),
          .FPB  (fpb)
      );
    end
    for (index = 0; index < PADS_PER_RAIL; index = index + 1) begin : u_vss_pads
      tenon_ics55_pvss1 #(
          .USE_PADI(index % 2)
      ) u_cell (
          .VSSD (iovss),
          .VSS  (vss),
          .VDD25(iovdd),
          .VDD  (vdd),
          .FPB  (fpb),
          .FP   (fp)
      );
    end
  endgenerate

  tenon_ics55_pb4 #(
      .USE_PADI(0)
  ) u_mgmt_clk_pad (
      .vssd       (iovss),
      .vss        (vss),
      .pad        (mgmt_clk_pad),
      .core_oe    (1'b0),
      .pad_to_core(mgmt_clk_i),
      .core_to_pad(1'b0),
      .vdd25      (iovdd),
      .vdd        (vdd),
      .fp         (fp),
      .fpb        (fpb)
  );
  tenon_ics55_pb4 #(
      .USE_PADI(1)
  ) u_mgmt_rst_n_pad (
      .vssd       (iovss),
      .vss        (vss),
      .pad        (mgmt_rst_n_pad),
      .core_oe    (1'b0),
      .pad_to_core(mgmt_rst_ni),
      .core_to_pad(1'b0),
      .vdd25      (iovdd),
      .vdd        (vdd),
      .fp         (fp),
      .fpb        (fpb)
  );
  tenon_ics55_pb4 #(
      .USE_PADI(0)
  ) u_jtag_tck_pad (
      .vssd       (iovss),
      .vss        (vss),
      .pad        (jtag_tck_pad),
      .core_oe    (1'b0),
      .pad_to_core(jtag_tck_i),
      .core_to_pad(1'b0),
      .vdd25      (iovdd),
      .vdd        (vdd),
      .fp         (fp),
      .fpb        (fpb)
  );
  tenon_ics55_pb4 #(
      .USE_PADI(1)
  ) u_jtag_tms_pad (
      .vssd       (iovss),
      .vss        (vss),
      .pad        (jtag_tms_pad),
      .core_oe    (1'b0),
      .pad_to_core(jtag_tms_i),
      .core_to_pad(1'b0),
      .vdd25      (iovdd),
      .vdd        (vdd),
      .fp         (fp),
      .fpb        (fpb)
  );
  tenon_ics55_pb4 #(
      .USE_PADI(0)
  ) u_jtag_tdi_pad (
      .vssd       (iovss),
      .vss        (vss),
      .pad        (jtag_tdi_pad),
      .core_oe    (1'b0),
      .pad_to_core(jtag_tdi_i),
      .core_to_pad(1'b0),
      .vdd25      (iovdd),
      .vdd        (vdd),
      .fp         (fp),
      .fpb        (fpb)
  );
  tenon_ics55_pb4 #(
      .USE_PADI(1)
  ) u_jtag_tdo_pad (
      .vssd       (iovss),
      .vss        (vss),
      .pad        (jtag_tdo_pad),
      .core_oe    (1'b1),
      .pad_to_core(),
      .core_to_pad(jtag_tdo_o),
      .vdd25      (iovdd),
      .vdd        (vdd),
      .fp         (fp),
      .fpb        (fpb)
  );
  tenon_ics55_pb4 #(
      .USE_PADI(0)
  ) u_uart_rx_pad (
      .vssd       (iovss),
      .vss        (vss),
      .pad        (uart_rx_pad),
      .core_oe    (1'b0),
      .pad_to_core(uart_rx_i),
      .core_to_pad(1'b0),
      .vdd25      (iovdd),
      .vdd        (vdd),
      .fp         (fp),
      .fpb        (fpb)
  );
  tenon_ics55_pb4 #(
      .USE_PADI(1)
  ) u_uart_tx_pad (
      .vssd       (iovss),
      .vss        (vss),
      .pad        (uart_tx_pad),
      .core_oe    (1'b1),
      .pad_to_core(),
      .core_to_pad(uart_tx_o),
      .vdd25      (iovdd),
      .vdd        (vdd),
      .fp         (fp),
      .fpb        (fpb)
  );

  generate
    for (index = 0; index < GPIO_COUNT; index = index + 1) begin : u_gpio_pads
      tenon_ics55_pb4 #(
          .USE_PADI(index % 2)
      ) u_cell (
          .vssd       (iovss),
          .vss        (vss),
          .pad        (gpio_pad[index]),
          .core_oe    (gpio_oe[index]),
          .pad_to_core(gpio_i[index]),
          .core_to_pad(gpio_o[index]),
          .vdd25      (iovdd),
          .vdd        (vdd),
          .fp         (fp),
          .fpb        (fpb)
      );
    end
  endgenerate
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
  wire fp;
  wire fpb;

  tenon_tier0_padframe_ics55_pb4_legacy #(
      .GPIO_COUNT   (GPIO_COUNT),
      .PADS_PER_RAIL(PADS_PER_RAIL)
  ) u_base (
      .iovdd,
      .iovss,
      .vdd,
      .vss,
      .fp,
      .fpb,
      .*
  );
endmodule

module tenon_tier0_padframe_ics55_pll #(
    parameter integer GPIO_COUNT    = 10,
    parameter integer PADS_PER_RAIL = 2
) (
    inout  wire                  iovdd,
    inout  wire                  iovss,
    inout  wire                  vdd,
    inout  wire                  vss,
    inout  wire                  pll_avdd,
    inout  wire                  pll_avss,
    inout  wire                  pll_avddio,
    inout  wire                  pll_avssio,
    inout  wire                  mgmt_clk_pad,
    inout  wire                  mgmt_rst_n_pad,
    inout  wire                  jtag_tck_pad,
    inout  wire                  jtag_tms_pad,
    inout  wire                  jtag_tdi_pad,
    inout  wire                  jtag_tdo_pad,
    inout  wire                  uart_rx_pad,
    inout  wire                  uart_tx_pad,
    inout  wire                  pll_xin_pad,
    inout  wire                  pll_xout_pad,
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
    input  wire [GPIO_COUNT-1:0] gpio_oe,
    input  wire                  pll_osc_enable,
    input  wire                  pll_en,
    input  wire                  pll_bp,
    input  wire                  pll_select,
    input  wire [           7:0] pll_n,
    input  wire [           1:0] pll_od,
    output wire                  pll_ckout1,
    output wire                  pll_ckout2,
    output wire                  pll_cktest
);
  wire pll_refclk;

  tenon_tier0_padframe_ics55_pb4_legacy #(
      .GPIO_COUNT   (GPIO_COUNT),
      .PADS_PER_RAIL(PADS_PER_RAIL)
  ) u_base (
      .fp (iovdd),
      .fpb(iovss),
      .*
  );

  tenon_ics55_pvss1cap #(.USE_PADI(1)) u_pll_avss_pad (.rail(pll_avss));
  tenon_ics55_pvdd1cap #(.USE_PADI(0)) u_pll_avdd_pad (.rail(pll_avdd));
  tenon_ics55_pvss3ap #(.USE_PADI(1)) u_pll_avssio_pad (.rail(pll_avssio));
  tenon_ics55_pvdd3ap #(.USE_PADI(0)) u_pll_avddio_pad (.rail(pll_avddio));
  tenon_ics55_pxwe1 u_pll_osc_pad (
      .vssd    (iovss),
      .vss     (vss),
      .vdd25   (iovdd),
      .vdd     (vdd),
      .fp      (iovdd),
      .fpb     (iovss),
      .xin_pad (pll_xin_pad),
      .xout_pad(pll_xout_pad),
      .enable  (pll_osc_enable),
      .refclk  (pll_refclk)
  );

  (* keep = "true" *) PLL_TOP u_pll (
      .BP      (pll_bp),
      .CKOUT1  (pll_ckout1),
      .CKOUT2  (pll_ckout2),
      .CKTST   (pll_cktest),
      .EN      (pll_en),
      .N       (pll_n),
      .OD      (pll_od),
      .REFCLK  (pll_refclk),
      .SELECT  (pll_select),
      .AVDD    (pll_avdd),
      .AVSS    (pll_avss),
      .DVDD    (vdd),
      .DVDD_DRV(vdd),
      .DVSS    (vss),
      .DVSS_DRV(vss)
  );
endmodule

`default_nettype wire
