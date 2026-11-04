`timescale 1ns/1ps
module tb_laser_protocol;
    reg clk=0; always #5 clk=~clk;
    reg rst_n=0,start=0;
    reg [9:0] cfg_x0=0,cfg_x1=640,cfg_min_run=0;
    reg [8:0] cfg_y0=0,cfg_y1=480;
    reg [15:0] cfg_threshold_q8=3072;
    wire busy,done,error; wire [3:0] error_code;
    reg [31:0] s_axis_tdata=0; reg [3:0] s_axis_tkeep=15;
    reg s_axis_tvalid=0,s_axis_tlast=0;wire s_axis_tready;
    wire [31:0] m_axis_tdata;wire [3:0] m_axis_tkeep;
    wire m_axis_tvalid,m_axis_tlast;reg m_axis_tready=0;
    laser_stripe_core dut(.*);

    integer received=0,frame_id=0,timeout_cycles;
    reg expect_positive=0,collecting=0,hold_output=0;
    reg [31:0] held_data;reg held_last;reg [3:0] held_keep;
    reg [31:0] expected_word;
    always @(posedge clk) begin
        if(!rst_n) hold_output=0;
        else begin
            if(hold_output && (!m_axis_tvalid || m_axis_tdata!==held_data || m_axis_tlast!==held_last || m_axis_tkeep!==held_keep))
                $fatal(1,"Protocol: output not stable under stall");
            hold_output=m_axis_tvalid && !m_axis_tready;
            held_data=m_axis_tdata;held_last=m_axis_tlast;held_keep=m_axis_tkeep;
            if(m_axis_tvalid && m_axis_tready) begin
                if(!collecting) $fatal(1,"Protocol: unsolicited output");
                expected_word=(expect_positive && received<12)?32'h80006600:0;
                if(m_axis_tdata!==expected_word || m_axis_tlast!==(received==639) || m_axis_tkeep!==15)
                    $fatal(1,"Protocol: frame=%0d column=%0d actual=%h expected=%h",frame_id,received,m_axis_tdata,expected_word);
                received=received+1;
            end
        end
    end
    task automatic reset_dut;
        @(negedge clk);rst_n=0;start=0;s_axis_tvalid=0;m_axis_tready=0;
        repeat(3) @(negedge clk);rst_n=1;
        repeat(2) @(negedge clk);
        if(busy || done || error || m_axis_tvalid) $fatal(1,"Reset failed");
    endtask
    task automatic launch(input integer mr);
        cfg_x0=0;cfg_x1=12;cfg_y0=0;cfg_y1=480;cfg_threshold_q8=3072;cfg_min_run=mr;
        @(negedge clk);start=1;@(negedge clk);start=0;
    endtask
    task automatic send_frame(input reg positive,input reg missing_last);
        for(integer i=0;i<307200;i=i+1) begin
            @(negedge clk);s_axis_tvalid=1;s_axis_tkeep=15;s_axis_tlast=(i==307199)&&!missing_last;
            s_axis_tdata=(positive && i/640>=100 && i/640<=104)?32'h00c81414:32'h00141414;
            @(posedge clk);if(!s_axis_tready) $fatal(1,"Unexpected input stall");
        end
        @(negedge clk);s_axis_tvalid=0;s_axis_tlast=0;
    endtask
    task automatic wait_output;
        timeout_cycles=0;
        while(!m_axis_tvalid) begin
            @(negedge clk);timeout_cycles=timeout_cycles+1;
            if(error || timeout_cycles>100000) $fatal(1,"Output wait failed");
        end
    endtask
    initial begin
        reset_dut();
        // Reset while capturing.
        launch(0);
        repeat(10) begin @(negedge clk);s_axis_tvalid=1;@(posedge clk);end
        reset_dut();
        // Reset while processing a complete captured image.
        launch(0);send_frame(1,0);repeat(1000) @(negedge clk);reset_dut();
        // Reset while an output is stalled; must cancel the old frame.
        launch(0);send_frame(1,0);wait_output();repeat(32) @(negedge clk);reset_dut();
        // Missing TLAST is rejected on accepted word 307200, no normal completion.
        launch(0);send_frame(0,1);
        if(!error || error_code!=3 || busy || done) $fatal(1,"Missing TLAST not rejected");
        // Ten continuous transactions, alternating positive/negative and core/full.
        for(frame_id=0;frame_id<10;frame_id=frame_id+1) begin
            received=0;collecting=1;expect_positive=(frame_id%2==0);
            launch((frame_id%4<2)?0:8);
            m_axis_tready=0;send_frame(expect_positive,0);wait_output();
            repeat(80) @(negedge clk);
            m_axis_tready=1;timeout_cycles=0;
            while(!done) begin
                @(negedge clk);timeout_cycles=timeout_cycles+1;
                if(error || timeout_cycles>2000) $fatal(1,"Completion timeout");
            end
            if(received!=640 || busy) $fatal(1,"Output count wrong");
            collecting=0;$display("PROTOCOL_FRAME_DONE %0d positive=%0d",frame_id,expect_positive);
        end
        $display("PROTOCOL_SUITE_PASSED reset_capture=1 reset_processing=1 reset_stalled_output=1 missing_tlast=1 continuous_frames=10");
        $finish;
    end
    initial begin #200000000; $fatal(1,"Protocol global timeout");end
endmodule
