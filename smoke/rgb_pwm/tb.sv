`default_nettype none
module rgb_pwm_tb;
  logic [7:0] ui_in = 8'b11111100, uio_in = 8'b00000111;
  logic ena = 1'b1, clk = 1'b0, rst_n = 1'b0;
  wire [7:0] uo_out, uio_out, uio_oe;
  tri VDD, VSS;
  rgb_pwm dut (.*);
  always #1 clk = !clk;
  initial begin
    repeat (2) @(posedge clk);
    rst_n = 1'b1;
    @(posedge clk);
    #1;
    if (uo_out[2:0] != 3'b111) $fatal(1, "PWM duty outputs are not active");
    $finish;
  end
endmodule
