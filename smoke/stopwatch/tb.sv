`default_nettype none
module stopwatch_tb;
  logic [7:0] ui_in = 8'b00000001, uio_in = 8'd0;
  logic ena = 1'b1, clk = 1'b0, rst_n = 1'b0;
  wire [7:0] uo_out, uio_out, uio_oe;
  tri VDD, VSS;
  stopwatch dut (.*);
  always #1 clk = !clk;
  initial begin
    repeat (2) @(posedge clk);
    rst_n = 1'b1;
    repeat (4) @(posedge clk);
    #1;
    if (uo_out < 8'd4) $fatal(1, "Stopwatch did not count");
    $finish;
  end
endmodule
