`default_nettype none
module led_matrix_scan_tb;
  logic [7:0] ui_in = 8'b00000001, uio_in = 8'd0; logic ena = 1'b1, clk = 1'b0, rst_n = 1'b0;
  wire [7:0] uo_out, uio_out, uio_oe; tri VDD, VSS;
  led_matrix_scan dut (.*);
  always #1 clk = !clk;
  initial begin repeat (2) @(posedge clk); rst_n = 1'b1; #1; if (uo_out != 8'h01 || uio_out != 8'h01 || uio_oe != 8'hff) $fatal(1, "Matrix row zero was not observable"); $finish; end
endmodule
