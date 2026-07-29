`default_nettype none
module sevenseg_counter_tb;
  logic [7:0] ui_in = 8'b00011010, uio_in = 8'd0;
  logic ena = 1'b1, clk = 1'b0, rst_n = 1'b0;
  wire [7:0] uo_out, uio_out, uio_oe;
  tri VDD, VSS;
  sevenseg_counter dut (.*);
  always #1 clk = !clk;
  initial begin
    repeat (2) @(posedge clk);
    rst_n = 1'b1;
    @(posedge clk);
    #1;
    if (uo_out[6:0] != 7'b1110111) $fatal(1, "Seven segment load failed");
    $finish;
  end
endmodule
