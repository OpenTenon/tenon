`default_nettype none

module tenon_mini_root_controller #(
    parameter int ADDRESS_WIDTH = 11
) (
    input  logic                     ctrl_clk_i,
    input  logic                     ctrl_rst_n_i,
    input  logic [ADDRESS_WIDTH-1:0] project_addr_i,
    input  logic                     requested_valid_i,
    input  logic                     select_commit_i,
    input  logic                     fabric_enable_i,
    input  logic [1:0]               cluster_clock_active_i,
    input  logic [1:0]               branch_clock_active_i,
    output logic [ADDRESS_WIDTH-1:0] active_address_o,
    output logic                     route_enable_o,
    output logic                     cluster_clock_request_o,
    output logic                     branch_clock_request_o,
    output logic                     select_ready_o,
    output logic                     select_valid_o
);

    typedef enum logic [2:0] {
        IDLE,
        WAIT_BRANCH_OFF,
        WAIT_CLUSTER_OFF,
        WAIT_CLUSTER_ON,
        WAIT_BRANCH_ON
    } state_t;

    state_t state;
    logic [ADDRESS_WIDTH-1:0] pending_address;
    logic pending_valid;
    logic active_valid;

    assign select_ready_o = state == IDLE;
    assign select_valid_o = active_valid;

    always_ff @(posedge ctrl_clk_i or negedge ctrl_rst_n_i) begin
        if (!ctrl_rst_n_i) begin
            state                     <= IDLE;
            pending_address           <= '0;
            pending_valid             <= 1'b0;
            active_address_o          <= '0;
            active_valid              <= 1'b0;
            route_enable_o            <= 1'b0;
            cluster_clock_request_o   <= 1'b0;
            branch_clock_request_o    <= 1'b0;
        end else begin
            case (state)
                IDLE: begin
                    if (select_commit_i) begin
                        pending_address        <= project_addr_i;
                        pending_valid          <= requested_valid_i;
                        route_enable_o         <= 1'b0;
                        branch_clock_request_o <= 1'b0;
                        state                  <= WAIT_BRANCH_OFF;
                    end else if (!fabric_enable_i &&
                                 (route_enable_o || branch_clock_request_o ||
                                  cluster_clock_request_o)) begin
                        pending_address        <= active_address_o;
                        pending_valid          <= active_valid;
                        route_enable_o         <= 1'b0;
                        branch_clock_request_o <= 1'b0;
                        state                  <= WAIT_BRANCH_OFF;
                    end else if (fabric_enable_i && active_valid && !route_enable_o) begin
                        cluster_clock_request_o <= 1'b1;
                        state                   <= WAIT_CLUSTER_ON;
                    end
                end
                WAIT_BRANCH_OFF: begin
                    if (branch_clock_active_i == 2'b00) begin
                        cluster_clock_request_o <= 1'b0;
                        state                   <= WAIT_CLUSTER_OFF;
                    end
                end
                WAIT_CLUSTER_OFF: begin
                    if (cluster_clock_active_i == 2'b00) begin
                        active_address_o <= pending_address;
                        active_valid     <= pending_valid;
                        if (pending_valid && fabric_enable_i) begin
                            cluster_clock_request_o <= 1'b1;
                            state                   <= WAIT_CLUSTER_ON;
                        end else begin
                            state <= IDLE;
                        end
                    end
                end
                WAIT_CLUSTER_ON: begin
                    if (!fabric_enable_i) begin
                        cluster_clock_request_o <= 1'b0;
                        state                   <= WAIT_CLUSTER_OFF;
                    end else if (cluster_clock_active_i == 2'b11) begin
                        branch_clock_request_o <= 1'b1;
                        state                  <= WAIT_BRANCH_ON;
                    end
                end
                WAIT_BRANCH_ON: begin
                    if (!fabric_enable_i) begin
                        branch_clock_request_o <= 1'b0;
                        state                  <= WAIT_BRANCH_OFF;
                    end else if (branch_clock_active_i == 2'b11) begin
                        route_enable_o <= 1'b1;
                        state          <= IDLE;
                    end
                end
                default: begin
                    state                   <= IDLE;
                    route_enable_o          <= 1'b0;
                    cluster_clock_request_o <= 1'b0;
                    branch_clock_request_o  <= 1'b0;
                end
            endcase
        end
    end

endmodule

`default_nettype wire
