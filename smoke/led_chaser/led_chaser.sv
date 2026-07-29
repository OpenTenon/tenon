// SPDX-License-Identifier: Apache-2.0
`default_nettype none
module led_chaser (
    inout wire VDD, inout wire VSS,
    input wire [7:0] ui_in, output logic [7:0] uo_out,
    input wire [7:0] uio_in, output logic [7:0] uio_out, output logic [7:0] uio_oe,
    input wire ena, input wire clk, input wire rst_n
);
  logic [15:0] divider;
  logic [2:0] position;
  always_ff @(posedge clk) begin
    if (!rst_n) begin divider <= 16'd0; position <= 3'd0; end
    else if (ena && ui_in[0]) begin
      divider <= divider + 16'd1;
      if (divider[7:0] == 8'hff) begin
        if (ui_in[1]) position <= position - 3'd1;
        else position <= position + 3'd1;
      end
    end
  end
  always_comb begin
    uo_out = 8'b00000001 << position;
    uio_out = {divider[15:11], uio_in[2:0]};
    uio_oe = 8'hf8;
  end
endmodule
