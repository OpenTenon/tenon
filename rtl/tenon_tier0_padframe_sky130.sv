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

  // The IO cell supplies its own static one and zero sources, matching the
  // Tiny Tapeout OpenFrame loopback model without routing controls through core.
  (* keep = "true" *)wire       control_low;
  (* keep = "true" *)wire       control_high;
  (* keep = "true" *)wire [2:0] drive_mode;
  assign drive_mode = {control_high, control_high, control_low};

  (* keep = "true" *) sky130_fd_io__top_gpiov2 u_pad (
      .PAD             (pad),
      .OUT             (core_to_pad),
      .OE_N            (~core_oe),
      .IN              (pad_to_core),
      .IN_H            (),
      .DM              (drive_mode),
      .HLD_H_N         (control_high),
      .INP_DIS         (control_low),
      .IB_MODE_SEL     (control_low),
      .ENABLE_H        (control_high),
      .ENABLE_VDDA_H   (control_high),
      .ENABLE_INP_H    (control_high),
      .TIE_HI_ESD      (control_high),
      .TIE_LO_ESD      (control_low),
      .SLOW            (control_low),
      .VTRIP_SEL       (control_low),
      .HLD_OVR         (control_low),
      .ANALOG_EN       (control_low),
      .ANALOG_SEL      (control_low),
      .ENABLE_VDDIO    (control_high),
      .ENABLE_VSWITCH_H(control_high),
      .ANALOG_POL      (control_low),
      .AMUXBUS_A       (),
      .AMUXBUS_B       (),
      .PAD_A_NOESD_H   (),
      .PAD_A_ESD_0_H   (),
      .PAD_A_ESD_1_H   (),
      .VCCD            (vccd),
      .VSSD            (vssd),
      .VDDIO           (vddio),
      .VSSIO           (vssio),
      .VDDIO_Q         (vddio),
      .VCCHIB          (vddio),
      .VDDA            (vddio),
      .VSSA            (vssd),
      .VSWITCH         (vddio),
      .VSSIO_Q         (vssio)
  );
endmodule

module tenon_tier0_padframe_sky130 #(
    parameter integer GPIO_COUNT    = 16,
    parameter integer PADS_PER_RAIL = 2
) (
    // Repeated package supply pads share one external net per rail.
    inout  wire                  vccd,
    inout  wire                  vssd,
    inout  wire                  vddio,
    inout  wire                  vssio,
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

  generate
    for (index = 0; index < PADS_PER_RAIL; index = index + 1) begin : u_iovdd_pads
      (* keep = "true" *) sky130_fd_io__top_power_hvc_wpad u_pad (
          .P_PAD      (vddio),
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
          .VDDIO_Q    (vddio),
          .VCCHIB     (vddio),
          .VDDA       (vddio),
          .VSSA       (vssd),
          .VSWITCH    (vddio),
          .VSSIO_Q    (vssio)
      );
    end
    for (index = 0; index < PADS_PER_RAIL; index = index + 1) begin : u_iovss_pads
      (* keep = "true" *) sky130_fd_io__top_ground_hvc_wpad u_pad (
          .G_PAD      (vssio),
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
          .VDDIO_Q    (vddio),
          .VCCHIB     (vddio),
          .VDDA       (vddio),
          .VSSA       (vssd),
          .VSWITCH    (vddio),
          .VSSIO_Q    (vssio)
      );
    end
    for (index = 0; index < PADS_PER_RAIL; index = index + 1) begin : u_vdd_pads
      (* keep = "true" *) sky130_fd_io__top_power_hvc_wpad u_pad (
          .P_PAD      (vccd),
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
          .VDDIO_Q    (vddio),
          .VCCHIB     (vddio),
          .VDDA       (vddio),
          .VSSA       (vssd),
          .VSWITCH    (vddio),
          .VSSIO_Q    (vssio)
      );
    end
    for (index = 0; index < PADS_PER_RAIL; index = index + 1) begin : u_vss_pads
      (* keep = "true" *) sky130_fd_io__top_ground_hvc_wpad u_pad (
          .G_PAD      (vssd),
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
          .VDDIO_Q    (vddio),
          .VCCHIB     (vddio),
          .VDDA       (vddio),
          .VSSA       (vssd),
          .VSWITCH    (vddio),
          .VSSIO_Q    (vssio)
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
