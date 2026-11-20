`default_nettype none

module tenon_mini_branch (
    input  logic [3:0]      site_addr_i,
    input  logic            route_enable_i,
    input  logic [15:0]     anchor_valid_i,
    input  logic [16*12-1:0] site_gpio_o_i,
    input  logic [16*12-1:0] site_gpio_oe_i,
    output logic [15:0]     site_enable_o,
    output logic [11:0]     gpio_o,
    output logic [11:0]     gpio_oe
);

    always_comb begin
        site_enable_o = '0;
        gpio_o         = '0;
        gpio_oe        = '0;
        if (route_enable_i && anchor_valid_i[site_addr_i]) begin
            site_enable_o[site_addr_i] = 1'b1;
            gpio_o = site_gpio_o_i[site_addr_i*12+:12];
            gpio_oe = site_gpio_oe_i[site_addr_i*12+:12];
        end
    end

endmodule

`default_nettype wire
