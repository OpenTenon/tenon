`default_nettype none

module tenon_mini_reference_project (
    input  logic [ 1:0] clk_i,
    input  logic [ 1:0] rst_n_i,
    input  logic        enable_i,
    input  logic [11:0] gpio_i,
    output logic [11:0] gpio_o,
    output logic [11:0] gpio_oe,
    inout  wire         VDD,
    inout  wire         VSS
);

  logic [5:0] lower_state;
  logic [5:0] upper_state;
  always_ff @(posedge clk_i[0] or negedge rst_n_i[0]) begin
    if (!rst_n_i[0]) begin
      lower_state <= '0;
    end else if (enable_i) begin
      lower_state <= gpio_i[5:0];
    end
  end

  always_ff @(posedge clk_i[1] or negedge rst_n_i[1]) begin
    if (!rst_n_i[1]) begin
      upper_state <= '0;
    end else if (enable_i) begin
      upper_state <= gpio_i[11:6];
    end
  end

  assign gpio_o  = {upper_state, lower_state};
  assign gpio_oe = {12{enable_i}};

endmodule

`default_nettype wire
