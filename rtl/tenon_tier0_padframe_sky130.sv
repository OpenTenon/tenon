// SPDX-License-Identifier: Apache-2.0
// Sky130A Tier0 adapter using sky130_fd_io power-aware PadCells.

`default_nettype none

module tenon_sky130_gpio (
    inout  wire pad,
    output wire pad_to_core,
    input  wire core_to_pad,
    input  wire core_oe,
    inout  wire vccd,
    inout  wire vssd,
    inout  wire vddio,
    inout  wire vssio
);
  wire vddio_q = vddio;
  wire vcchib = vddio;
  wire vdda = vddio;
  wire vswitch = vddio;
  wire vssa = vssd;
  wire vssio_q = vssio;

  (* keep = "true" *) sky130_fd_io__top_gpiov2 u_pad (
      .PAD             (pad),
      .OUT             (core_to_pad),
      .OE_N            (~core_oe),
      .IN              (pad_to_core),
      .IN_H            (),
      .DM              (3'b110),
      .HLD_H_N         (1'b1),
      .INP_DIS         (1'b0),
      .IB_MODE_SEL     (1'b0),
      .ENABLE_H        (1'b1),
      .ENABLE_VDDA_H   (1'b1),
      .ENABLE_INP_H    (1'b1),
      .TIE_HI_ESD      (),
      .TIE_LO_ESD      (),
      .SLOW            (1'b0),
      .VTRIP_SEL       (1'b0),
      .HLD_OVR         (1'b0),
      .ANALOG_EN       (1'b0),
      .ANALOG_SEL      (1'b0),
      .ENABLE_VDDIO    (1'b1),
      .ENABLE_VSWITCH_H(1'b1),
      .ANALOG_POL      (1'b0),
      .AMUXBUS_A       (),
      .AMUXBUS_B       (),
      .PAD_A_NOESD_H   (),
      .PAD_A_ESD_0_H   (),
      .PAD_A_ESD_1_H   (),
      .VCCD            (vccd),
      .VSSD            (vssd),
      .VDDIO           (vddio),
      .VSSIO           (vssio),
      .VDDIO_Q         (vddio_q),
      .VCCHIB          (vcchib),
      .VDDA            (vdda),
      .VSSA            (vssa),
      .VSWITCH         (vswitch),
      .VSSIO_Q         (vssio_q)
  );
endmodule

module tenon_tier0_padframe_sky130 #(
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
  wire vccd;
  wire vssd;
  wire vddio;
  wire vssio;
  wire vddio_q = vddio;
  wire vcchib = vddio;
  wire vdda = vddio;
  wire vswitch = vddio;
  wire vssa = vssd;
  wire vssio_q = vssio;
  genvar index;

  generate
    for (index = 0; index < PADS_PER_RAIL; index = index + 1) begin : u_iovdd_pads
      (* keep = "true" *) sky130_fd_io__top_power_hvc_wpad u_pad (
          .P_PAD      (),
          .P_CORE     (vddio),
          .AMUXBUS_A  (),
          .AMUXBUS_B  (),
          .DRN_HVC    (),
          .OGC_HVC    (),
          .SRC_BDY_HVC(),
          .VCCD       (vccd),
          .VSSD       (vssd),
          .VDDIO      (vddio),
          .VSSIO      (vssio),
          .VDDIO_Q    (vddio_q),
          .VCCHIB     (vcchib),
          .VDDA       (vdda),
          .VSSA       (vssa),
          .VSWITCH    (vswitch),
          .VSSIO_Q    (vssio_q)
      );
    end
    for (index = 0; index < PADS_PER_RAIL; index = index + 1) begin : u_iovss_pads
      (* keep = "true" *) sky130_fd_io__top_ground_hvc_wpad u_pad (
          .G_PAD      (),
          .G_CORE     (vssio),
          .AMUXBUS_A  (),
          .AMUXBUS_B  (),
          .DRN_HVC    (),
          .OGC_HVC    (),
          .SRC_BDY_HVC(),
          .VCCD       (vccd),
          .VSSD       (vssd),
          .VDDIO      (vddio),
          .VSSIO      (vssio),
          .VDDIO_Q    (vddio_q),
          .VCCHIB     (vcchib),
          .VDDA       (vdda),
          .VSSA       (vssa),
          .VSWITCH    (vswitch),
          .VSSIO_Q    (vssio_q)
      );
    end
    for (index = 0; index < PADS_PER_RAIL; index = index + 1) begin : u_vdd_pads
      (* keep = "true" *) sky130_fd_io__top_power_hvc_wpad u_pad (
          .P_PAD      (),
          .P_CORE     (vccd),
          .AMUXBUS_A  (),
          .AMUXBUS_B  (),
          .DRN_HVC    (),
          .OGC_HVC    (),
          .SRC_BDY_HVC(),
          .VCCD       (vccd),
          .VSSD       (vssd),
          .VDDIO      (vddio),
          .VSSIO      (vssio),
          .VDDIO_Q    (vddio_q),
          .VCCHIB     (vcchib),
          .VDDA       (vdda),
          .VSSA       (vssa),
          .VSWITCH    (vswitch),
          .VSSIO_Q    (vssio_q)
      );
    end
    for (index = 0; index < PADS_PER_RAIL; index = index + 1) begin : u_vss_pads
      (* keep = "true" *) sky130_fd_io__top_ground_hvc_wpad u_pad (
          .G_PAD      (),
          .G_CORE     (vssd),
          .AMUXBUS_A  (),
          .AMUXBUS_B  (),
          .DRN_HVC    (),
          .OGC_HVC    (),
          .SRC_BDY_HVC(),
          .VCCD       (vccd),
          .VSSD       (vssd),
          .VDDIO      (vddio),
          .VSSIO      (vssio),
          .VDDIO_Q    (vddio_q),
          .VCCHIB     (vcchib),
          .VDDA       (vdda),
          .VSSA       (vssa),
          .VSWITCH    (vswitch),
          .VSSIO_Q    (vssio_q)
      );
    end
  endgenerate

  tenon_sky130_gpio u_mgmt_clk_pad (
      .pad        (mgmt_clk_pad),
      .pad_to_core(mgmt_clk_i),
      .core_to_pad(1'b0),
      .core_oe    (1'b0),
      .vccd       (vccd),
      .vssd       (vssd),
      .vddio      (vddio),
      .vssio      (vssio)
  );
  tenon_sky130_gpio u_mgmt_rst_n_pad (
      .pad        (mgmt_rst_n_pad),
      .pad_to_core(mgmt_rst_ni),
      .core_to_pad(1'b0),
      .core_oe    (1'b0),
      .vccd       (vccd),
      .vssd       (vssd),
      .vddio      (vddio),
      .vssio      (vssio)
  );
  tenon_sky130_gpio u_jtag_tck_pad (
      .pad        (jtag_tck_pad),
      .pad_to_core(jtag_tck_i),
      .core_to_pad(1'b0),
      .core_oe    (1'b0),
      .vccd       (vccd),
      .vssd       (vssd),
      .vddio      (vddio),
      .vssio      (vssio)
  );
  tenon_sky130_gpio u_jtag_tms_pad (
      .pad        (jtag_tms_pad),
      .pad_to_core(jtag_tms_i),
      .core_to_pad(1'b0),
      .core_oe    (1'b0),
      .vccd       (vccd),
      .vssd       (vssd),
      .vddio      (vddio),
      .vssio      (vssio)
  );
  tenon_sky130_gpio u_jtag_tdi_pad (
      .pad        (jtag_tdi_pad),
      .pad_to_core(jtag_tdi_i),
      .core_to_pad(1'b0),
      .core_oe    (1'b0),
      .vccd       (vccd),
      .vssd       (vssd),
      .vddio      (vddio),
      .vssio      (vssio)
  );
  tenon_sky130_gpio u_jtag_tdo_pad (
      .pad        (jtag_tdo_pad),
      .pad_to_core(),
      .core_to_pad(jtag_tdo_o),
      .core_oe    (1'b1),
      .vccd       (vccd),
      .vssd       (vssd),
      .vddio      (vddio),
      .vssio      (vssio)
  );
  tenon_sky130_gpio u_uart_rx_pad (
      .pad        (uart_rx_pad),
      .pad_to_core(uart_rx_i),
      .core_to_pad(1'b0),
      .core_oe    (1'b0),
      .vccd       (vccd),
      .vssd       (vssd),
      .vddio      (vddio),
      .vssio      (vssio)
  );
  tenon_sky130_gpio u_uart_tx_pad (
      .pad        (uart_tx_pad),
      .pad_to_core(),
      .core_to_pad(uart_tx_o),
      .core_oe    (1'b1),
      .vccd       (vccd),
      .vssd       (vssd),
      .vddio      (vddio),
      .vssio      (vssio)
  );

  generate
    for (index = 0; index < GPIO_COUNT; index = index + 1) begin : u_gpio_pads
      tenon_sky130_gpio u_pad (
          .pad        (gpio_pad[index]),
          .pad_to_core(gpio_i[index]),
          .core_to_pad(gpio_o[index]),
          .core_oe    (gpio_oe[index]),
          .vccd       (vccd),
          .vssd       (vssd),
          .vddio      (vddio),
          .vssio      (vssio)
      );
    end
  endgenerate
endmodule

`default_nettype wire
