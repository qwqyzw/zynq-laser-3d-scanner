`timescale 1ns/1ps
`include "project_paths.svh"
module tb_laser_stripe;
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
    reg [31:0] pixels [0:307199];
    reg [31:0] random_state=32'h13579bdf;
    integer cases_fd,output_fd,rc,case_number=0,received=0,cycles=0;
    integer xx0,xx1,yy0,yy1,th,mr,send_idx,case_cycles,case_limit;
    string root,stem,mode,input_name,output_name,case_path,vector_dir,output_dir;
    integer paths_fd;
    reg collecting=0,hold_output=0;
    reg [31:0] held_data;reg held_last;reg [3:0] held_keep;
    integer stall_remaining=0;reg long_stall_started=0;
    integer reset_tests=0,limit_fd;
    always @(negedge clk) begin
        random_state={random_state[30:0],random_state[31]^random_state[21]^random_state[1]^random_state[0]};
        if(!rst_n) m_axis_tready=0;
        else if(collecting && received==100 && !long_stall_started) begin
            stall_remaining=200;long_stall_started=1;m_axis_tready=0;
        end else if(stall_remaining>0) begin
            stall_remaining=stall_remaining-1;m_axis_tready=0;
        end else m_axis_tready=(random_state[2:0]!=0);
    end
    always @(posedge clk) begin
        cycles=cycles+1;
        if(rst_n) begin
            if(hold_output && (!m_axis_tvalid || m_axis_tdata!==held_data ||
                m_axis_tlast!==held_last || m_axis_tkeep!==held_keep))
                $fatal(1,"Output changed during backpressure");
            hold_output=m_axis_tvalid && !m_axis_tready;
            held_data=m_axis_tdata;held_last=m_axis_tlast;held_keep=m_axis_tkeep;
            if(m_axis_tvalid && m_axis_tready) begin
                if(!collecting) $fatal(1,"Unexpected output");
                if(m_axis_tkeep!==4'hf || m_axis_tlast!==(received==639))
                    $fatal(1,"Output keep/last mismatch at %0d",received);
                if(m_axis_tdata[30:18]!==0 || (!m_axis_tdata[31] && m_axis_tdata!==0)
                    || (m_axis_tdata[31] && m_axis_tdata[17:0]>=122880))
                    $fatal(1,"Output format invalid at %0d",received);
                $fdisplay(output_fd,"%08x",m_axis_tdata);
                received=received+1;
            end
        end else hold_output=0;
    end
    task automatic pulse_reset;
        @(negedge clk);rst_n=0;s_axis_tvalid=0;start=0;
        repeat(3) @(negedge clk);
        rst_n=1;repeat(2) @(negedge clk);
        if(busy || done || error || m_axis_tvalid) $fatal(1,"Reset state failed");
    endtask
    task automatic launch;
        @(negedge clk);start=1;
        @(negedge clk);start=0;
    endtask
    task automatic send_one(input reg last_flag,input reg [3:0] keep_flag);
        @(negedge clk);s_axis_tdata=0;s_axis_tvalid=1;s_axis_tlast=last_flag;s_axis_tkeep=keep_flag;
        @(posedge clk);if(!s_axis_tready) $fatal(1,"Input not ready");
        @(negedge clk);s_axis_tvalid=0;s_axis_tlast=0;s_axis_tkeep=15;
    endtask
    initial begin
        if(!$value$plusargs("ROOT=%s",root)) root=`LASER_PROJECT_ROOT;
        case_limit=46;limit_fd=$fopen("run_limit.txt","r");
        if(limit_fd) begin rc=$fscanf(limit_fd,"%d",case_limit);$fclose(limit_fd);end
        case_path={root,"/tb/cases.txt"};vector_dir={`LASER_BASELINE_ROOT,"/vectors"};output_dir={root,"/results/rtl_out"};
        paths_fd=$fopen("run_paths.txt","r");
        if(paths_fd) begin rc=$fscanf(paths_fd,"%s %s %s",case_path,vector_dir,output_dir);$fclose(paths_fd);end
        pulse_reset();
        // Invalid ROI, invalid mode, malformed keep, early TLAST and reset recovery.
        cfg_x0=10;cfg_x1=10;launch();
        if(!error || error_code!=1 || busy) $fatal(1,"Invalid ROI not rejected");
        cfg_x0=0;cfg_x1=640;cfg_min_run=7;launch();
        if(!error || error_code!=1) $fatal(1,"Invalid mode not rejected");
        cfg_min_run=0;launch();send_one(0,4'h7);
        if(!error || error_code!=2 || busy) $fatal(1,"Bad TKEEP not rejected");
        launch();send_one(1,4'hf);
        if(!error || error_code!=3 || busy) $fatal(1,"Early TLAST not rejected");
        launch();send_one(0,4'hf);pulse_reset();reset_tests=1;
        $display("DIRECTED_CONTROL_TESTS_PASSED");
        cases_fd=$fopen(case_path,"r");
        if(!cases_fd) $fatal(1,"Cannot open case list");
        while(!$feof(cases_fd) && case_number<case_limit) begin
            rc=$fscanf(cases_fd,"%s %s %d %d %d %d %d %d\n",stem,mode,xx0,yy0,xx1,yy1,th,mr);
            if(rc!=8) $fatal(1,"Malformed case list rc=%0d",rc);
            input_name={vector_dir,"/",stem,"_input.hex"};
            output_name={output_dir,"/",stem,"_",mode,".hex"};
            $readmemh(input_name,pixels);
            output_fd=$fopen(output_name,"w");
            if(!output_fd) $fatal(1,"Cannot create output");
            cfg_x0=xx0;cfg_x1=xx1;cfg_y0=yy0;cfg_y1=yy1;
            cfg_threshold_q8=th;cfg_min_run=mr;
            received=0;collecting=1;long_stall_started=0;stall_remaining=0;
            case_cycles=cycles;launch();
            for(send_idx=0;send_idx<307200;) begin
                @(negedge clk);
                s_axis_tvalid=(random_state[6:3]!=0);
                s_axis_tdata=pixels[send_idx];s_axis_tlast=(send_idx==307199);
                // Mutate external configuration mid-frame: current result must stay frozen.
                if(send_idx==100) begin cfg_x0=639;cfg_x1=0;cfg_y0=479;cfg_y1=0;cfg_threshold_q8=65535;cfg_min_run=7;end
                @(posedge clk);
                if(s_axis_tvalid && s_axis_tready) send_idx=send_idx+1;
                if(error) $fatal(1,"DUT input error %0d",error_code);
            end
            @(negedge clk);s_axis_tvalid=0;s_axis_tlast=0;
            while(!done) begin
                @(negedge clk);
                if(error) $fatal(1,"DUT processing error %0d",error_code);
                if(cycles-case_cycles>2500000) $fatal(1,"Frame timeout state=%0d",dut.state);
            end
            if(received!=640 || busy) $fatal(1,"Incorrect output count %0d",received);
            collecting=0;$fclose(output_fd);case_number=case_number+1;
            $display("CASE_DONE %0d %s %s cycles=%0d",case_number,stem,mode,cycles-case_cycles);
        end
        $fclose(cases_fd);
        $display("RTL_SIMULATION_PASSED cases=%0d directed_control=%0d",case_number,reset_tests);
        $finish;
    end
    initial begin #1500000000; $fatal(1,"Global timeout");end
endmodule
