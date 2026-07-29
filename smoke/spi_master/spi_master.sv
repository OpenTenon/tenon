// SPDX-License-Identifier: Apache-2.0
`default_nettype none
module spi_master (
    inout  wire        VDD,
    inout  wire        VSS,
    input  wire  [7:0] ui_in,
    output logic [7:0] uo_out,
    input  wire  [7:0] uio_in,
    output logic [7:0] uio_out,
    output logic [7:0] uio_oe,
    input  wire        ena,
    input  wire        clk,
    input  wire        rst_n
);
  logic [7:0] shift;
  logic [2:0] divider;
  logic [3:0] bit_count;
  logic busy, sclk;
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      shift     <= 8'd0;
      divider   <= 3'd0;
      bit_count <= 4'd0;
      busy      <= 1'b0;
      sclk      <= 1'b0;
    end else if (ena) begin
      if (!busy && uio_in[0]) begin
        shift     <= ui_in;
        divider   <= 3'd0;
        bit_count <= 4'd0;
        busy      <= 1'b1;
        sclk      <= 1'b0;
      end else if (busy) begin
        divider <= divider + 3'd1;
        if (divider == 3'd7) begin
          sclk <= !sclk;
          if (sclk) begin
            shift     <= {shift[6:0], uio_in[1]};
            bit_count <= bit_count + 4'd1;
            if (bit_count == 4'd7) busy <= 1'b0;
          end
        end
      end
    end
  end
  always_comb begin
    uo_out  = {bit_count, busy, !busy, shift[7], sclk};
    uio_out = {6'b0, uio_in[1], busy};
    uio_oe  = 8'h01;
  end
endmodule
