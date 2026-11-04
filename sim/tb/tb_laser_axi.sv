`include "project_paths.svh"
`timescale 1ns/1ps
// This bench never opens expected output files. Frozen comparison happens in Python.
module tb_laser_axi;
    reg aclk=0; always #5 aclk=~aclk;
    reg aresetn=0;
    reg [15:0] s_axi_awaddr=0,s_axi_araddr=0;
    reg [2:0] s_axi_awprot=0,s_axi_arprot=0;
    reg s_axi_awvalid=0,s_axi_wvalid=0,s_axi_bready=0;
    reg s_axi_arvalid=0,s_axi_rready=0;
    wire s_axi_awready,s_axi_wready,s_axi_bvalid,s_axi_arready,s_axi_rvalid;
    reg [31:0] s_axi_wdata=0;
    reg [3:0] s_axi_wstrb=0;
    wire [1:0] s_axi_bresp,s_axi_rresp;
    wire [31:0] s_axi_rdata;
    reg [31:0] s_axis_tdata=0;
    reg [3:0] s_axis_tkeep=15;
    reg s_axis_tlast=0,s_axis_tvalid=0;
    wire s_axis_tready;
    wire [31:0] m_axis_tdata;
    wire [3:0] m_axis_tkeep;
    wire m_axis_tlast,m_axis_tvalid;
    reg m_axis_tready=0;
    laser_stripe_axi dut(.*);

    reg [31:0] pixels[0:307199];
    integer received=0,output_fd=0,check_synthetic=0;
    integer write_transactions=0,read_transactions=0,b_handshakes=0,r_handshakes=0;
    integer stream_frames=0,test_groups=0,watchdog=0;
    reg collecting=0,aborting=0;
    reg hold_b=0,hold_r=0,hold_stream=0;
    reg [1:0] held_bresp,held_rresp;
    reg [31:0] held_rdata,held_stream_data;
    reg [3:0] held_keep;
    reg held_last;
    reg [31:0] expected_word,status_word,read_value,last_cycles;
    string root,output_dir,vector_dir;
    integer paths_fd,rc;

    always @(posedge aclk) begin
        if(!aresetn) begin
            hold_b=0;hold_r=0;hold_stream=0;
        end else begin
            if(hold_b && (!s_axi_bvalid || s_axi_bresp!==held_bresp))
                $fatal(1,"AXI B channel changed while stalled");
            if(hold_r && (!s_axi_rvalid || s_axi_rdata!==held_rdata || s_axi_rresp!==held_rresp))
                $fatal(1,"AXI R channel changed while stalled");
            if(hold_stream && !aborting && (!m_axis_tvalid || m_axis_tdata!==held_stream_data || m_axis_tkeep!==held_keep || m_axis_tlast!==held_last))
                $fatal(1,"AXIS output changed while stalled");
            hold_b=s_axi_bvalid && !s_axi_bready;held_bresp=s_axi_bresp;
            hold_r=s_axi_rvalid && !s_axi_rready;held_rdata=s_axi_rdata;held_rresp=s_axi_rresp;
            hold_stream=m_axis_tvalid && !m_axis_tready;
            held_stream_data=m_axis_tdata;held_keep=m_axis_tkeep;held_last=m_axis_tlast;
            if(s_axi_bvalid && s_axi_bready) b_handshakes=b_handshakes+1;
            if(s_axi_rvalid && s_axi_rready) r_handshakes=r_handshakes+1;
            if(m_axis_tvalid && m_axis_tready) begin
                if(!collecting) $fatal(1,"Unsolicited AXIS output");
                if(received>=640 || m_axis_tkeep!==15 || m_axis_tlast!==(received==639))
                    $fatal(1,"AXIS packet format/count failure column=%0d",received);
                if(m_axis_tdata[30:18]!==0 || (!m_axis_tdata[31] && m_axis_tdata!==0) || (m_axis_tdata[31] && m_axis_tdata[17:0]>=122880))
                    $fatal(1,"AXIS coordinate format failure");
                if(check_synthetic!=0) begin
                    expected_word=(check_synthetic==1 && received<12)?32'h80006600:0;
                    if(m_axis_tdata!==expected_word)
                        $fatal(1,"Synthetic frame mismatch column=%0d got=%h expected=%h",received,m_axis_tdata,expected_word);
                end
                if(output_fd) $fdisplay(output_fd,"%08x",m_axis_tdata);
                received=received+1;
            end
        end
    end

    task automatic axi_write(input reg [15:0] addr,input reg [31:0] value,input reg [3:0] strobe,input integer aw_delay,input integer w_delay,input integer stall,input reg [1:0] expected_resp);
        integer timeout_b;
        fork
            begin
                repeat(aw_delay+1) @(negedge aclk);
                s_axi_awaddr=addr;s_axi_awvalid=1;
                @(posedge aclk);while(!s_axi_awready) @(posedge aclk);
                @(negedge aclk);s_axi_awvalid=0;
            end
            begin
                repeat(w_delay+1) @(negedge aclk);
                s_axi_wdata=value;s_axi_wstrb=strobe;s_axi_wvalid=1;
                @(posedge aclk);while(!s_axi_wready) @(posedge aclk);
                @(negedge aclk);s_axi_wvalid=0;
            end
        join
        timeout_b=0;
        while(!s_axi_bvalid) begin
            @(negedge aclk);timeout_b=timeout_b+1;
            if(timeout_b>100) $fatal(1,"AXI write timeout addr=%h",addr);
        end
        repeat(stall) @(negedge aclk);
        if(s_axi_bresp!==expected_resp) $fatal(1,"BRESP addr=%h data=%h actual=%h expected=%h",addr,value,s_axi_bresp,expected_resp);
        s_axi_bready=1;@(posedge aclk);
        if(!s_axi_bvalid) $fatal(1,"Lost B response");
        @(negedge aclk);s_axi_bready=0;
        write_transactions=write_transactions+1;
    endtask

    task automatic axi_read(input reg [15:0] addr,input integer stall,input reg [1:0] expected_resp,output reg [31:0] value);
        integer timeout_r;
        @(negedge aclk);s_axi_araddr=addr;s_axi_arvalid=1;
        @(posedge aclk);while(!s_axi_arready) @(posedge aclk);
        @(negedge aclk);s_axi_arvalid=0;timeout_r=0;
        while(!s_axi_rvalid) begin
            @(negedge aclk);timeout_r=timeout_r+1;
            if(timeout_r>100) $fatal(1,"AXI read timeout addr=%h",addr);
        end
        repeat(stall) @(negedge aclk);
        if(s_axi_rresp!==expected_resp) $fatal(1,"RRESP addr=%h actual=%h expected=%h",addr,s_axi_rresp,expected_resp);
        value=s_axi_rdata;s_axi_rready=1;@(posedge aclk);
        if(!s_axi_rvalid) $fatal(1,"Lost R response");
        @(negedge aclk);s_axi_rready=0;read_transactions=read_transactions+1;
    endtask

    task automatic expect_read(input reg[15:0] addr,input reg[31:0] expected);
        reg [31:0] actual;
        axi_read(addr,$urandom_range(0,9),0,actual);
        if(actual!==expected) $fatal(1,"Register addr=%h actual=%h expected=%h",addr,actual,expected);
    endtask
    task automatic expect_status(input reg[31:0] mask,input reg[31:0] expected);
        reg [31:0] actual;
        axi_read(16'h04,2,0,actual);
        if((actual&mask)!==expected) $fatal(1,"STATUS actual=%h mask=%h expected=%h",actual,mask,expected);
    endtask
    task automatic write_reg(input reg[15:0] addr,input reg[31:0] value);
        axi_write(addr,value,15,$urandom_range(0,6),$urandom_range(0,6),$urandom_range(0,10),0);
    endtask
    task automatic config_roi(input integer x0,input integer y0,input integer x1,input integer y1,input integer mr);
        write_reg(16'h10,x0);write_reg(16'h14,y0);write_reg(16'h18,x1);write_reg(16'h1c,y1);
        write_reg(16'h20,3072);write_reg(16'h24,mr);
    endtask
    task automatic clear_status;
        write_reg(0,4);repeat(3) @(negedge aclk);expect_status(32'hf0f,0);
    endtask
    task automatic invalid_config(input reg[15:0] addr,input reg[31:0] bad,input reg[31:0] restore_value);
        write_reg(addr,bad);axi_write(0,1,15,0,5,4,2);
        expect_status(32'hf0f,32'h104);clear_status();write_reg(addr,restore_value);
    endtask
    task automatic global_reset;
        @(negedge aclk);aresetn=0;s_axis_tvalid=0;s_axis_tlast=0;m_axis_tready=0;
        s_axi_awvalid=0;s_axi_wvalid=0;s_axi_arvalid=0;s_axi_bready=0;s_axi_rready=0;
        repeat(5) @(negedge aclk);aresetn=1;repeat(5) @(negedge aclk);
    endtask
    task automatic send_bad_word(input reg last_flag,input reg[3:0] keep_flag);
        @(negedge aclk);s_axis_tdata=0;s_axis_tlast=last_flag;s_axis_tkeep=keep_flag;s_axis_tvalid=1;
        @(posedge aclk);if(!s_axis_tready) $fatal(1,"Capture not ready for error injection");
        @(negedge aclk);s_axis_tvalid=0;s_axis_tlast=0;s_axis_tkeep=15;
        repeat(5) @(negedge aclk);
    endtask
    task automatic send_frame(input integer source);
        integer idx,timeout_input;
        for(idx=0;idx<307200;idx=idx+1) begin
            @(negedge aclk);s_axis_tvalid=1;s_axis_tkeep=15;s_axis_tlast=(idx==307199);
            if(source==0) s_axis_tdata=pixels[idx];
            else s_axis_tdata=(source==1 && idx/640>=100 && idx/640<=104)?32'h00c81414:32'h00141414;
            timeout_input=0;
            @(posedge aclk);
            while(!s_axis_tready) begin
                @(posedge aclk);timeout_input=timeout_input+1;
                if(timeout_input>100) $fatal(1,"Unexpected capture stall idx=%0d",idx);
            end
            if(idx%997==0) begin @(negedge aclk);s_axis_tvalid=0;end
        end
        @(negedge aclk);s_axis_tvalid=0;s_axis_tlast=0;
    endtask
    task automatic wait_output;
        integer n;
        n=0;while(!m_axis_tvalid) begin
            @(negedge aclk);n=n+1;
            if(n>1500000) $fatal(1,"Processing timeout");
        end
    endtask
    task automatic drain_frame(input integer count_expected);
        integer n;
        n=0;
        while(received<640) begin
            @(negedge aclk);m_axis_tready=($urandom_range(0,7)!=0);n=n+1;
            if(n>2000) $fatal(1,"Drain timeout count=%0d",received);
        end
        @(negedge aclk);m_axis_tready=0;collecting=0;
        repeat(5) @(negedge aclk);
        expect_status(32'hf0f,2);expect_read(16'h38,count_expected);
        axi_read(16'h3c,2,0,last_cycles);
        if(last_cycles<307200) $fatal(1,"Frame cycle counter too small %0d",last_cycles);
        expect_read(16'h40,0);
        repeat(20) @(negedge aclk);expect_status(32'hf0f,2);
        stream_frames=stream_frames+1;
    endtask
    task automatic abort_frame(input integer completed_expected);
        aborting=1;write_reg(0,2);repeat(8) @(negedge aclk);
        if(m_axis_tvalid || s_axis_tready) $fatal(1,"ABORT did not cancel core transaction");
        hold_stream=0;aborting=0;expect_status(32'hf0f,0);expect_read(16'h38,completed_expected);
        expect_read(16'h3c,0);expect_read(16'h40,0);
    endtask

    initial begin
        if(!$value$plusargs("ROOT=%s",root)) root=`LASER_PROJECT_ROOT;
        output_dir={root,"/results/axi_out"};vector_dir={`LASER_BASELINE_ROOT,"/vectors"};
        paths_fd=$fopen("run_paths.txt","r");
        if(paths_fd) begin rc=$fscanf(paths_fd,"%s %s",vector_dir,output_dir);$fclose(paths_fd);end
        global_reset();
        expect_read(16'h08,32'h4c535431);expect_read(16'h0c,32'h00010000);
        expect_read(16'h10,0);expect_read(16'h14,0);expect_read(16'h18,640);expect_read(16'h1c,480);
        expect_read(16'h20,3072);expect_read(16'h24,8);
        expect_read(16'h28,1228800);expect_read(16'h2c,2560);expect_read(16'h30,640);expect_read(16'h34,480);
        expect_read(16'h38,0);expect_read(16'h3c,0);expect_read(16'h40,0);expect_status(32'hf0f,0);

        // Sparse and zero strobes preserve all unselected bytes; AW/W arrive independently.
        write_reg(16'h10,32'h11223344);
        axi_write(16'h10,32'haabbccdd,4'b0101,0,13,14,0);expect_read(16'h10,32'h11bb33dd);
        axi_write(16'h10,32'hffffffff,0,11,0,7,0);expect_read(16'h10,32'h11bb33dd);
        for(integer i=0;i<48;i=i+1) begin
            write_reg(16'h20,32'h12340000+i);expect_read(16'h20,32'h12340000+i);
        end
        config_roi(0,0,640,480,8);
        axi_write(0,1,0,0,9,4,0);expect_status(32'hf0f,0);
        axi_write(0,1,4'b1000,10,0,4,0);expect_status(32'hf0f,0);
        axi_write(0,32'hffffffff,4'b1000,10,0,4,2);expect_status(32'hf0f,0);
        for(integer i=0;i<3;i=i+1) begin
            axi_write(16'h11+i,0,15,i,3-i,3,2);
            axi_read(16'h11+i,3,2,read_value);
        end
        axi_write(16'h44,0,15,0,4,4,2);axi_read(16'h44,4,2,read_value);
        axi_write(16'h100,0,15,5,0,4,2);axi_read(16'h100,4,2,read_value);
        expect_read(16'h10,0);
        axi_write(16'h08,0,15,0,4,2,2);axi_write(16'h04,0,15,4,0,2,2);
        axi_write(16'h38,0,15,1,3,2,2);expect_read(0,0);
        axi_write(0,3,15,0,5,4,2);axi_write(0,8,15,5,0,4,2);
        expect_status(32'hf0f,0);test_groups=test_groups+1;
        $display("AXI_REGISTER_PROTOCOL_PASSED writes=%0d reads=%0d",write_transactions,read_transactions);

        invalid_config(16'h10,32'h00000400,0);
        invalid_config(16'h14,32'h00000200,0);
        invalid_config(16'h18,641,640);invalid_config(16'h1c,481,480);
        invalid_config(16'h20,32'h00010c00,3072);invalid_config(16'h24,7,8);
        invalid_config(16'h10,640,0);
        write_reg(0,1);send_bad_word(1,15);expect_status(32'hf0f,32'h304);clear_status();
        write_reg(0,1);send_bad_word(0,7);expect_status(32'hf0f,32'h204);clear_status();
        write_reg(0,1);send_bad_word(0,15);abort_frame(0);expect_read(16'h24,8);
        test_groups=test_groups+1;$display("AXI_ERROR_AND_CAPTURE_ABORT_PASSED");

        // Real frozen image: shadow mutation and rejected busy START must leave active values intact.
        $readmemh({vector_dir,"/flat_zero_input.hex"},pixels);
        config_roi(155,235,355,300,0);write_reg(0,1);expect_status(32'hf0f,1);
        received=0;collecting=1;check_synthetic=0;
        output_fd=$fopen({output_dir,"/flat_zero_core.hex"},"w");if(!output_fd) $fatal(1,"Cannot create actual file");
        fork
            send_frame(0);
            begin
                repeat(100) @(negedge aclk);
                write_reg(16'h10,639);write_reg(16'h18,0);write_reg(16'h14,479);write_reg(16'h1c,0);
                write_reg(16'h20,65535);write_reg(16'h24,7);
                // CLEAR acknowledges sticky flags while preserving the active transaction.
                write_reg(0,4);expect_status(32'hf0f,1);
                fork
                    axi_write(0,1,15,0,10,5,2);
                    expect_read(16'h08,32'h4c535431);
                join
                expect_status(32'hf0f,1);
            end
        join
        wait_output();repeat(80) @(negedge aclk);drain_frame(1);
        $fclose(output_fd);output_fd=0;
        expect_read(16'h10,639);expect_read(16'h24,7);write_reg(0,4);expect_status(32'hf0f,0);
        expect_read(16'h3c,last_cycles);expect_read(16'h38,1);
        $display("AXI_REAL_FRAME_DONE flat_zero core");

        config_roi(155,235,355,300,8);write_reg(0,1);
        received=0;collecting=1;check_synthetic=0;
        output_fd=$fopen({output_dir,"/flat_zero_full.hex"},"w");if(!output_fd) $fatal(1,"Cannot create actual file");
        send_frame(0);wait_output();repeat(30) @(negedge aclk);drain_frame(2);
        $fclose(output_fd);output_fd=0;$display("AXI_REAL_FRAME_DONE flat_zero full");
        test_groups=test_groups+1;

        // Abort an actual completed computation before output consumption, preserving completed count/config.
        config_roi(0,0,12,480,8);write_reg(0,1);send_frame(1);wait_output();
        repeat(80) @(negedge aclk);abort_frame(2);expect_read(16'h18,12);expect_read(16'h24,8);
        // Restart twice, with positive and negative images in consecutive transactions.
        write_reg(0,1);received=0;collecting=1;check_synthetic=1;
        send_frame(1);wait_output();drain_frame(3);
        config_roi(0,0,12,480,0);write_reg(0,1);received=0;collecting=1;check_synthetic=2;
        send_frame(2);wait_output();drain_frame(4);test_groups=test_groups+1;
        $display("AXI_OUTPUT_ABORT_RESTART_PASSED synthetic_frames=2");
        // Duplicated requests/responses are detected by exact handshake accounting.
        repeat(20) @(negedge aclk);
        if(s_axi_bvalid || s_axi_rvalid || m_axis_tvalid) $fatal(1,"Unsolicited/duplicate response");
        if(b_handshakes!=write_transactions || r_handshakes!=read_transactions)
            $fatal(1,"Handshake count mismatch B=%0d/%0d R=%0d/%0d",b_handshakes,write_transactions,r_handshakes,read_transactions);
        global_reset();expect_status(32'hf0f,0);expect_read(16'h38,0);expect_read(16'h3c,0);expect_read(16'h40,0);
        expect_read(16'h10,0);expect_read(16'h18,640);expect_read(16'h24,8);
        $display("AXI_SUITE_PASSED groups=%0d frames=%0d writes=%0d reads=%0d",test_groups,stream_frames,write_transactions,read_transactions);
        $finish;
    end
    initial begin #150000000; $fatal(1,"AXI global timeout");end
endmodule
