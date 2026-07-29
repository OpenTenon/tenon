`default_nettype none
module uart_tx_tb;
  logic [7:0] ui_in = 8'ha5, uio_in = 8'd0;
  logic ena = 1'b1, clk = 1'b0, rst_n = 1'b0;
  wire [7:0] uo_out, uio_out, uio_oe;
  tri VDD, VSS;
  uart_tx dut (.*);
  always #1 clk = !clk;
  initial begin
    repeat (2) @(posedge clk);
    rst_n     = 1'b1;
    uio_in[0] = 1'b1;
    @(posedge clk);
    #1;
    uio_in[0] = 1'b0;
    if (!(uo_out[3] && !uo_out[0])) $fatal(1, "UART start frame failed");
    $finish;
  end
endmodule
