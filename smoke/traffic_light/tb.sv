`default_nettype none
module traffic_light_tb;
  logic [7:0] ui_in = 8'd0, uio_in = 8'd0; logic ena = 1'b1, clk = 1'b0, rst_n = 1'b0;
  wire [7:0] uo_out, uio_out, uio_oe; tri VDD, VSS;
  traffic_light dut (.*);
  always #1 clk = !clk;
  initial begin repeat (2) @(posedge clk); rst_n = 1'b1; @(posedge clk); #1; if (uo_out[2:0] != 3'd1) $fatal(1, "Traffic FSM did not enter yellow"); $finish; end
endmodule
