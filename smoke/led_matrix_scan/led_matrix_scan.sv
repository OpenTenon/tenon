// SPDX-License-Identifier: Apache-2.0
`default_nettype none
module led_matrix_scan (
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
  logic [15:0] divider;
  logic [ 2:0] row;
  function automatic logic [7:0] row_pattern(input logic [2:0] row_value, input logic [2:0] mode);
    case (mode)
      3'd0:    row_pattern = 8'b00000001 << row_value;
      3'd1:    row_pattern = 8'b10000000 >> row_value;
      3'd2:    row_pattern = row_value[0] ? 8'haa : 8'h55;
      3'd3:    row_pattern = (row_value == 3'd0 || row_value == 3'd7) ? 8'hff : 8'h81;
      default: row_pattern = 8'h18 << row_value[1:0];
    endcase
  endfunction
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      divider <= 16'd0;
      row     <= 3'd0;
    end else if (ena && ui_in[0]) begin
      divider <= divider + 16'd1;
      if (divider[7:0] == 8'hff) row <= row + 3'd1;
    end
  end
  always_comb begin
    uo_out  = row_pattern(row, ui_in[3:1]) ^ {8{uio_in[0]}};
    uio_out = 8'b00000001 << row;
    uio_oe  = 8'hff;
  end
endmodule
