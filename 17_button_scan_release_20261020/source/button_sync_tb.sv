`timescale 1ns/1ps
module button_sync_tb;
    reg aclk = 0;
    reg aresetn = 0;
    reg [3:0] buttons_async = 0;
    wire [3:0] buttons_sync;
    button_sync dut(.*);
    always #10 aclk = ~aclk;
    task expect_value(input [3:0] expected);
        begin
            @(posedge aclk); #1;
            if (buttons_sync !== expected)
                $fatal(1, "Expected %b got %b", expected, buttons_sync);
        end
    endtask
    initial begin
        expect_value(4'b0000);
        @(negedge aclk); aresetn = 1; buttons_async = 4'b0001;
        expect_value(4'b0000);
        expect_value(4'b0001);
        #3; buttons_async = 4'b1010;
        expect_value(4'b0001);
        expect_value(4'b1010);
        @(negedge aclk); aresetn = 0;
        expect_value(4'b0000);
        @(negedge aclk); aresetn = 1; buttons_async = 4'b1111;
        expect_value(4'b0000);
        expect_value(4'b1111);
        $display("BUTTON_SYNC_TWO_STAGE_AND_RESET_PASS");
        $finish;
    end
endmodule
