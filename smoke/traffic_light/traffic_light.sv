// SPDX-License-Identifier: Apache-2.0
`default_nettype none
module traffic_light (
    inout wire VDD, inout wire VSS,
    input wire [7:0] ui_in, output logic [7:0] uo_out,
    input wire [7:0] uio_in, output logic [7:0] uio_out, output logic [7:0] uio_oe,
    input wire ena, input wire clk, input wire rst_n
);
  typedef enum logic [2:0] {GREEN, YELLOW, RED, WALK} state_t;
  state_t state;
  logic [15:0] timer;
  logic walk_request;
  always_ff @(posedge clk) begin
    if (!rst_n) begin state <= GREEN; timer <= 16'd0; walk_request <= 1'b0; end
    else if (ena) begin
      if (ui_in[0]) walk_request <= 1'b1;
      if (ui_in[1]) begin state <= RED; timer <= 16'd0; end
      else if (timer[7:3] == ui_in[7:3]) begin
        timer <= 16'd0;
        case (state)
          GREEN: state <= YELLOW;
          YELLOW: state <= RED;
          RED: begin
            if (walk_request) state <= WALK;
            else state <= GREEN;
            walk_request <= 1'b0;
          end
          default: state <= GREEN;
        endcase
      end else timer <= timer + 16'd1;
    end
  end
  always_comb begin
    uo_out = {timer[4:0], state};
    uio_out = {walk_request, 4'b0, timer[10:8]};
    uio_oe = 8'hff;
  end
endmodule
