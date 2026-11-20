`default_nettype none

module tenon_mini_tb;

  localparam int TOTAL_SITES = 2048;

  logic          ctrl_clk_i = 1'b0;
  logic          ctrl_rst_n_i = 1'b1;
  logic   [10:0] project_addr_i = '0;
  logic          select_commit_i = 1'b0;
  logic          select_ready_o;
  logic          select_valid_o;
  logic          fabric_enable_i = 1'b0;
  logic   [ 1:0] clk_i = '0;
  logic   [ 1:0] rst_n_i = 2'b11;
  logic   [11:0] gpio_i = '0;
  logic   [11:0] gpio_o;
  logic   [11:0] gpio_oe;
  supply1        VDD;
  supply0        VSS;

  always #5 ctrl_clk_i = ~ctrl_clk_i;
  always #7 clk_i[0] = ~clk_i[0];
  always #11 clk_i[1] = ~clk_i[1];

  tenon_mini_top dut (
      .ctrl_clk_i,
      .ctrl_rst_n_i,
      .project_addr_i,
      .select_commit_i,
      .select_ready_o,
      .select_valid_o,
      .fabric_enable_i,
      .clk_i,
      .rst_n_i,
      .gpio_i,
      .gpio_o,
      .gpio_oe,
      .VDD,
      .VSS
  );

  task automatic wait_until_ready;
    integer cycle;
    begin : wait_loop
      for (cycle = 0; cycle < 400; cycle = cycle + 1) begin
        @(posedge ctrl_clk_i);
        if (select_ready_o) begin
          disable wait_loop;
        end
      end
      $display("DEBUG ready=%b state=%0d cluster_req=%b branch_req=%b cluster_ack=%b branch_ack=%b",
               select_ready_o, dut.root_fabric_I.controller_I.state,
               dut.root_fabric_I.cluster_clock_request, dut.root_fabric_I.branch_clock_request,
               dut.root_fabric_I.selected_cluster_clock_active,
               dut.root_fabric_I.selected_branch_clock_active);
      $fatal(1, "selection did not become ready");
    end
  endtask

  task automatic select_project(input logic [10:0] address);
    logic [TOTAL_SITES-1:0] expected_enable;
    begin
      wait_until_ready();
      project_addr_i  = address;
      select_commit_i = 1'b1;
      @(posedge ctrl_clk_i);
      #1 select_commit_i = 1'b0;
      @(posedge ctrl_clk_i);
      if (gpio_oe !== 12'h000) begin
        $fatal(1, "GPIO was driven during address break-before-make");
      end
      wait_until_ready();
      if (!select_valid_o) begin
        $fatal(1, "valid reference address %0d was rejected", address);
      end
      expected_enable          = '0;
      expected_enable[address] = 1'b1;
      if (dut.site_enable !== expected_enable) begin
        $fatal(1, "address %0d did not produce exactly one site enable", address);
      end
    end
  endtask

  integer address;
  initial begin
    #1;
    ctrl_rst_n_i = 1'b0;
    rst_n_i      = 2'b00;
    repeat (4) @(posedge ctrl_clk_i);
    ctrl_rst_n_i    = 1'b1;
    rst_n_i         = 2'b11;
    fabric_enable_i = 1'b1;

    select_project(11'd0);
    gpio_i = 12'ha5c;
    repeat (3) @(posedge clk_i[0]);
    repeat (3) @(posedge clk_i[1]);
    if (gpio_o !== 12'ha5c || gpio_oe !== 12'hfff) begin
      $fatal(1, "selected project did not observe both clock domains");
    end

    select_project({3'd5, 4'd9, 4'd4});

    if ($test$plusargs("FULL")) begin
      for (address = 0; address < TOTAL_SITES; address = address + 1) begin
        select_project(address[10:0]);
      end
    end else begin
      select_project(11'd1);
      select_project(11'd255);
      select_project(11'd256);
      select_project(11'd1023);
      select_project(11'd2047);
    end

    fabric_enable_i = 1'b0;
    wait_until_ready();
    if (gpio_oe !== 12'h000 || dut.site_enable !== '0) begin
      $fatal(1, "fabric disable did not isolate all projects");
    end

    fabric_enable_i = 1'b1;
    wait_until_ready();
    if (dut.site_enable[2047] !== 1'b1) begin
      $fatal(1, "fabric did not restore the selected project");
    end

    if ($test$plusargs("FULL")) begin
      $display("PASS: traversed all %0d Mini sites", TOTAL_SITES);
    end else begin
      $display("PASS: Mini dual-clock selection smoke simulation completed");
    end
    $finish;
  end

endmodule

`default_nettype wire
