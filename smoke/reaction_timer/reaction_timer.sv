// SPDX-License-Identifier: Apache-2.0
`default_nettype none
module reaction_timer (
    inout wire VDD, inout wire VSS,
    input wire [7:0] ui_in, output logic [7:0] uo_out,
    input wire [7:0] uio_in, output logic [7:0] uio_out, output logic [7:0] uio_oe,
    input wire ena, input wire clk, input wire rst_n
);
  typedef enum logic [1:0] {IDLE, WAITING, GO, DONE} state_t;
  state_t state;
  logic [15:0] wait_count, reaction_count;
  always_ff @(posedge clk) begin
    if (!rst_n) begin state <= IDLE; wait_count <= 16'd0; reaction_count <= 16'd0; end
    else if (ena) begin
      case (state)
        IDLE: if (ui_in[0]) begin state <= WAITING; wait_count <= 16'd0; end
        WAITING: begin
          wait_count <= wait_count + 16'd1;
          if (wait_count[7:1] >= ui_in[7:1]) begin state <= GO; reaction_count <= 16'd0; end
        end
        GO: if (uio_in[0]) state <= DONE; else reaction_count <= reaction_count + 16'd1;
        default: if (!ui_in[0]) state <= IDLE;
      endcase
    end
  end
  always_comb begin
    uo_out = {state, reaction_count[5:0]};
    uio_out = reaction_count[13:6];
    uio_oe = 8'hff;
  end
endmodule
