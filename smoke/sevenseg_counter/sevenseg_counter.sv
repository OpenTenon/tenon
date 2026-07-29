// SPDX-License-Identifier: Apache-2.0
`default_nettype none
module sevenseg_counter (
    inout wire VDD, inout wire VSS,
    input wire [7:0] ui_in, output logic [7:0] uo_out,
    input wire [7:0] uio_in, output logic [7:0] uio_out, output logic [7:0] uio_oe,
    input wire ena, input wire clk, input wire rst_n
);
  logic [15:0] count;
  logic [7:0] divider;
  function automatic logic [6:0] decode(input logic [3:0] value);
    case (value)
      4'h0: decode = 7'b0111111; 4'h1: decode = 7'b0000110; 4'h2: decode = 7'b1011011;
      4'h3: decode = 7'b1001111; 4'h4: decode = 7'b1100110; 4'h5: decode = 7'b1101101;
      4'h6: decode = 7'b1111101; 4'h7: decode = 7'b0000111; 4'h8: decode = 7'b1111111;
      4'h9: decode = 7'b1101111; 4'ha: decode = 7'b1110111; 4'hb: decode = 7'b1111100;
      4'hc: decode = 7'b0111001; 4'hd: decode = 7'b1011110; 4'he: decode = 7'b1111001;
      default: decode = 7'b1110001;
    endcase
  endfunction
  always_ff @(posedge clk) begin
    if (!rst_n) begin count <= 16'd0; divider <= 8'd0; end
    else if (ena) begin
      if (ui_in[4]) count <= {12'd0, ui_in[3:0]};
      else if (ui_in[5]) begin
        divider <= divider + 8'd1;
        if (divider == 8'hff) count <= count + 16'd1;
      end
    end
  end
  always_comb begin
    uo_out = {1'b0, decode(count[3:0])};
    uio_out = count[15:8];
    uio_oe = 8'hff;
  end
endmodule
