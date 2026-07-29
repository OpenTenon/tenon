// SPDX-License-Identifier: Apache-2.0
`default_nettype none
module uart_tx (
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
  logic [9:0] frame;
  logic [3:0] bit_index;
  logic [3:0] baud_count;
  logic       busy;
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      frame      <= 10'h3ff;
      bit_index  <= 4'd0;
      baud_count <= 4'd0;
      busy       <= 1'b0;
    end else if (ena) begin
      if (!busy && uio_in[0]) begin
        frame      <= {1'b1, ui_in, 1'b0};
        bit_index  <= 4'd0;
        baud_count <= 4'd0;
        busy       <= 1'b1;
      end else if (busy) begin
        baud_count <= baud_count + 4'd1;
        if (baud_count == 4'hf) begin
          baud_count <= 4'd0;
          frame      <= {1'b1, frame[9:1]};
          bit_index  <= bit_index + 4'd1;
          if (bit_index == 4'd9) busy <= 1'b0;
        end
      end
    end
  end
  always_comb begin
    uo_out  = {bit_index, busy, 2'b0, frame[0]};
    uio_out = {4'b0, baud_count};
    uio_oe  = 8'hf0;
  end
endmodule
