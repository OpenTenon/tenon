// SPDX-License-Identifier: Apache-2.0
`default_nettype none

module priority_encoder4to2 (
    inout  wire        VDD,
    inout  wire        VSS,
    input  wire  [3:0] request_i,
    output logic [1:0] code_o,
    output logic       valid_o
);
  always @* begin
    code_o  = 2'b00;
    valid_o = 1'b1;
    if (request_i[3]) begin
      code_o = 2'b11;
    end else if (request_i[2]) begin
      code_o = 2'b10;
    end else if (request_i[1]) begin
      code_o = 2'b01;
    end else if (request_i[0]) begin
      code_o = 2'b00;
    end else begin
      valid_o = 1'b0;
    end
  end
endmodule
