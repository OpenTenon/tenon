`default_nettype none

module tenon_mini_gateway (
    input  logic        selected_i,
    input  logic [11:0] fabric_gpio_i,
    output logic [11:0] project_gpio_i,
    output logic        project_enable_o,
    input  logic [11:0] project_gpio_o,
    input  logic [11:0] project_gpio_oe,
    output logic [11:0] site_gpio_o,
    output logic [11:0] site_gpio_oe
);

    assign project_enable_o = selected_i;
    assign project_gpio_i   = fabric_gpio_i & {12{selected_i}};
    assign site_gpio_o      = project_gpio_o & {12{selected_i}};
    assign site_gpio_oe     = project_gpio_oe & {12{selected_i}};

endmodule

`default_nettype wire
