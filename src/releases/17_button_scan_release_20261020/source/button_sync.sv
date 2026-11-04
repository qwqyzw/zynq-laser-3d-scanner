// Four asynchronous PYNQ-Z2 buttons, sampled into FCLK0 (50 MHz).
// Debounce and edge/long-press handling belong to the board service.
module button_sync (
    input  wire       aclk,
    input  wire       aresetn,
    input  wire [3:0]  buttons_async,
    output wire [3:0]  buttons_sync
);
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [3:0] sync_meta;
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [3:0] sync_stable;
    always @(posedge aclk) begin
        if (!aresetn) begin
            sync_meta <= 4'b0000;
            sync_stable <= 4'b0000;
        end else begin
            sync_meta <= buttons_async;
            sync_stable <= sync_meta;
        end
    end
    assign buttons_sync = sync_stable;
endmodule
