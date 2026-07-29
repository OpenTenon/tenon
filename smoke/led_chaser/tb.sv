`default_nettype none
module led_chaser_tb;
  logic [7:0] ui_in = 8'b00000001, uio_in = 8'd0; logic ena = 1'b1, clk = 1'b0, rst_n = 1'b0;
  wire [7:0] uo_out, uio_out, uio_oe; tri VDD, VSS;
  led_chaser dut (.*);
  always #1 clk = !clk;
  initial begin repeat (2) @(posedge clk); rst_n = 1'b1; repeat (256) @(posedge clk); #1; if (uo_out != 8'b00000010) $fatal(1, "LED did not advance"); $finish; end
endmodule
