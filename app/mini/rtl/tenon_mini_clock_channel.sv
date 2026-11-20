`default_nettype none

module tenon_mini_clock_channel #(
    parameter int DRIVE = 2
) (
    input  logic ctrl_clk_i,
    input  logic ctrl_rst_n_i,
    input  logic clock_i,
    input  logic request_i,
    output logic clock_o,
    output logic active_o
);

  (* async_reg = "true" *)logic [1:0] request_sync;
  (* async_reg = "true" *)logic [1:0] active_sync;

  always_ff @(posedge clock_i or negedge ctrl_rst_n_i) begin
    if (!ctrl_rst_n_i) begin
      request_sync <= '0;
    end else begin
      request_sync <= {request_sync[0], request_i};
    end
  end

  tenon_mini_clock_gate #(
      .DRIVE(DRIVE)
  ) gate_I (
      .clock_i,
      .reset_n_i(ctrl_rst_n_i),
      .enable_i (request_sync[1]),
      .clock_o
  );

  always_ff @(posedge ctrl_clk_i or negedge ctrl_rst_n_i) begin
    if (!ctrl_rst_n_i) begin
      active_sync <= '0;
    end else begin
      active_sync <= {active_sync[0], request_sync[1]};
    end
  end

  assign active_o = active_sync[1];

endmodule

`default_nettype wire
