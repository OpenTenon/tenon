`default_nettype none

module tenon_mini_clock_gate #(
    parameter int DRIVE = 2
) (
    input  logic clock_i,
    input  logic reset_n_i,
    input  logic enable_i,
    output logic clock_o
);

    localparam logic DRIVE_VALID = DRIVE > 0;
    logic enable_latched;

    always_ff @(negedge clock_i or negedge reset_n_i) begin
        if (!reset_n_i) begin
            enable_latched <= 1'b0;
        end else begin
            enable_latched <= enable_i;
        end
    end

    assign clock_o = clock_i & enable_latched & DRIVE_VALID;

endmodule

`default_nettype wire
