`default_nettype none
module lfsr_dice_tb;
  logic [7:0] ui_in = 8'd0, uio_in = 8'd0; logic ena = 1'b1, clk = 1'b0, rst_n = 1'b0;
  wire [7:0] uo_out, uio_out, uio_oe; tri VDD, VSS;
  lfsr_dice dut (.*);
  always #1 clk = !clk;
  initial begin repeat (2) @(posedge clk); if (uo_out != 8'hce) $fatal(1, "LFSR reset seed failed"); rst_n = 1'b1; @(posedge clk); #1; if (uo_out == 8'hce) $fatal(1, "LFSR did not advance"); $finish; end
endmodule
