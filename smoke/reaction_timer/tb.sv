`default_nettype none
module reaction_timer_tb;
  logic [7:0] ui_in = 8'b00000001, uio_in = 8'd0; logic ena = 1'b1, clk = 1'b0, rst_n = 1'b0;
  wire [7:0] uo_out, uio_out, uio_oe; tri VDD, VSS;
  reaction_timer dut (.*);
  always #1 clk = !clk;
  initial begin repeat (2) @(posedge clk); rst_n = 1'b1; repeat (3) @(posedge clk); #1; if (uo_out[7:6] != 2'd2) $fatal(1, "Reaction timer did not enter GO"); uio_in[0] = 1'b1; @(posedge clk); #1; if (uo_out[7:6] != 2'd3) $fatal(1, "Reaction timer did not stop"); $finish; end
endmodule
