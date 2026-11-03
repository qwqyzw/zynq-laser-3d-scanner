`timescale 1ns/1ps
// AXI4-Lite control wrapper; register ABI v1, horizontal_v4_20261019.
module laser_stripe_axi (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 aclk CLK" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME aclk, ASSOCIATED_BUSIF S_AXI:S_AXIS:M_AXIS, ASSOCIATED_RESET aresetn, FREQ_HZ 50000000" *)
    input wire aclk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 aresetn RST" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME aresetn, POLARITY ACTIVE_LOW" *)
    input wire aresetn,
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_AXI, PROTOCOL AXI4LITE, DATA_WIDTH 32, ADDR_WIDTH 16, FREQ_HZ 50000000" *)
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWADDR" *)
    input wire [15:0] s_axi_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWPROT" *)
    input wire [2:0] s_axi_awprot,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWVALID" *)
    input wire s_axi_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWREADY" *)
    output wire s_axi_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WDATA" *)
    input wire [31:0] s_axi_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WSTRB" *)
    input wire [3:0] s_axi_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WVALID" *)
    input wire s_axi_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WREADY" *)
    output wire s_axi_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BRESP" *)
    output reg [1:0] s_axi_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BVALID" *)
    output reg s_axi_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BREADY" *)
    input wire s_axi_bready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARADDR" *)
    input wire [15:0] s_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARPROT" *)
    input wire [2:0] s_axi_arprot,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARVALID" *)
    input wire s_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARREADY" *)
    output wire s_axi_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RDATA" *)
    output reg [31:0] s_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RRESP" *)
    output reg [1:0] s_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RVALID" *)
    output reg s_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RREADY" *)
    input wire s_axi_rready,
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_AXIS, TDATA_NUM_BYTES 4, HAS_TKEEP 1, HAS_TLAST 1, FREQ_HZ 50000000" *)
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TDATA" *)
    input wire [31:0] s_axis_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TKEEP" *)
    input wire [3:0] s_axis_tkeep,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TLAST" *)
    input wire s_axis_tlast,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TVALID" *)
    input wire s_axis_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TREADY" *)
    output wire s_axis_tready,
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME M_AXIS, TDATA_NUM_BYTES 4, HAS_TKEEP 1, HAS_TLAST 1, FREQ_HZ 50000000" *)
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TDATA" *)
    output wire [31:0] m_axis_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TKEEP" *)
    output wire [3:0] m_axis_tkeep,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TLAST" *)
    output wire m_axis_tlast,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TVALID" *)
    output wire m_axis_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TREADY" *)
    input wire m_axis_tready
);
    localparam [1:0] OKAY=2'b00, SLVERR=2'b10;
    reg aw_pending,w_pending;
    reg [15:0] aw_address;
    reg [31:0] w_data;
    reg [3:0] w_strobe;
    reg [31:0] cfg_x0,cfg_y0,cfg_x1,cfg_y1,cfg_threshold,cfg_min_run;
    reg [9:0] active_x0,active_x1,active_min_run;
    reg [8:0] active_y0,active_y1;
    reg [15:0] active_threshold;
    reg core_start,running,sticky_done,sticky_error;
    reg [3:0] sticky_code;
    reg [2:0] reset_count;
    reg core_done_d,core_error_d;
    reg [31:0] completed_frames;
    reg [63:0] elapsed_cycles;
    wire core_busy,core_done,core_error;
    wire [3:0] core_error_code;
    wire core_input_ready,core_output_valid,core_output_last;
    wire [31:0] core_output_data;
    wire [3:0] core_output_keep;
    wire core_reset_n=aresetn && (reset_count==0);
    wire effective_busy=running || core_busy || (reset_count!=0);
    wire write_commit=aw_pending && w_pending && !s_axi_bvalid;
    wire [31:0] byte_mask={{8{w_strobe[3]}},{8{w_strobe[2]}},{8{w_strobe[1]}},{8{w_strobe[0]}}};
    wire [31:0] command=w_data & byte_mask;
    wire abort_commit=write_commit && aw_address==16'h0000 && command==32'h2;
    wire config_valid=(cfg_x0<cfg_x1 && cfg_x1<=640 && cfg_y0<cfg_y1 && cfg_y1<=480
        && cfg_threshold<=65535 && (cfg_min_run==0 || cfg_min_run==8));
    function automatic [31:0] merge_bytes(input [31:0] previous,input [31:0] value,input [3:0] strobe);
        integer i;
        begin
            merge_bytes=previous;
            for(i=0;i<4;i=i+1) if(strobe[i]) merge_bytes[i*8+:8]=value[i*8+:8];
        end
    endfunction
    assign s_axi_awready=aresetn && !aw_pending && !s_axi_bvalid;
    assign s_axi_wready=aresetn && !w_pending && !s_axi_bvalid;
    assign s_axi_arready=aresetn && !s_axi_rvalid;
    assign s_axis_tready=core_reset_n && running && core_input_ready;
    assign m_axis_tvalid=core_reset_n && running && core_output_valid;
    assign m_axis_tdata=core_output_data;
    assign m_axis_tkeep=core_output_keep;
    assign m_axis_tlast=core_output_last;
    laser_stripe_core core (
        .clk(aclk),.rst_n(core_reset_n),.start(core_start),
        .cfg_x0(active_x0),.cfg_x1(active_x1),.cfg_y0(active_y0),.cfg_y1(active_y1),
        .cfg_threshold_q8(active_threshold),.cfg_min_run(active_min_run),
        .busy(core_busy),.done(core_done),.error(core_error),.error_code(core_error_code),
        .s_axis_tdata(s_axis_tdata),.s_axis_tkeep(s_axis_tkeep),.s_axis_tlast(s_axis_tlast),
        .s_axis_tvalid(s_axis_tvalid && running && core_reset_n),.s_axis_tready(core_input_ready),
        .m_axis_tdata(core_output_data),.m_axis_tkeep(core_output_keep),.m_axis_tlast(core_output_last),
        .m_axis_tvalid(core_output_valid),.m_axis_tready(m_axis_tready && running && core_reset_n)
    );
    always @(posedge aclk) begin
        if(!aresetn) begin
            aw_pending<=0;w_pending<=0;aw_address<=0;w_data<=0;w_strobe<=0;
            s_axi_bvalid<=0;s_axi_bresp<=OKAY;s_axi_rvalid<=0;s_axi_rresp<=OKAY;s_axi_rdata<=0;
            cfg_x0<=0;cfg_y0<=0;cfg_x1<=640;cfg_y1<=480;cfg_threshold<=3072;cfg_min_run<=8;
            active_x0<=0;active_y0<=0;active_x1<=640;active_y1<=480;active_threshold<=3072;active_min_run<=8;
            core_start<=0;running<=0;sticky_done<=0;sticky_error<=0;sticky_code<=0;
            reset_count<=0;core_done_d<=0;core_error_d<=0;completed_frames<=0;elapsed_cycles<=0;
        end else begin
            core_start<=0;
            if(reset_count!=0) reset_count<=reset_count-1;
            if(core_reset_n) begin core_done_d<=core_done;core_error_d<=core_error;end
            else begin core_done_d<=0;core_error_d<=0;end
            if(running) elapsed_cycles<=elapsed_cycles+1;
            if(s_axi_bvalid && s_axi_bready) s_axi_bvalid<=0;
            if(s_axi_rvalid && s_axi_rready) s_axi_rvalid<=0;
            // AW and W are captured independently, in either arrival order.
            if(s_axi_awvalid && s_axi_awready) begin aw_address<=s_axi_awaddr;aw_pending<=1;end
            if(s_axi_wvalid && s_axi_wready) begin w_data<=s_axi_wdata;w_strobe<=s_axi_wstrb;w_pending<=1;end
            if(write_commit) begin
                aw_pending<=0;w_pending<=0;s_axi_bvalid<=1;s_axi_bresp<=OKAY;
                if(aw_address[1:0]!=0) s_axi_bresp<=SLVERR;
                else case(aw_address)
                    16'h0000: case(command)
                        32'h0: begin end
                        32'h1: begin
                            if(effective_busy) s_axi_bresp<=SLVERR;
                            else if(!config_valid) begin
                                sticky_done<=0;sticky_error<=1;sticky_code<=1;elapsed_cycles<=0;
                                s_axi_bresp<=SLVERR;
                            end else begin
                                active_x0<=cfg_x0[9:0];active_y0<=cfg_y0[8:0];
                                active_x1<=cfg_x1[9:0];active_y1<=cfg_y1[8:0];
                                active_threshold<=cfg_threshold[15:0];active_min_run<=cfg_min_run[9:0];
                                sticky_done<=0;sticky_error<=0;sticky_code<=0;
                                elapsed_cycles<=0;running<=1;core_start<=1;
                            end
                        end
                        32'h2: begin
                            reset_count<=4;core_start<=0;running<=0;
                            sticky_done<=0;sticky_error<=0;sticky_code<=0;elapsed_cycles<=0;
                        end
                        32'h4: begin sticky_done<=0;sticky_error<=0;sticky_code<=0;end
                        default:s_axi_bresp<=SLVERR;
                    endcase
                    16'h0010:cfg_x0<=merge_bytes(cfg_x0,w_data,w_strobe);
                    16'h0014:cfg_y0<=merge_bytes(cfg_y0,w_data,w_strobe);
                    16'h0018:cfg_x1<=merge_bytes(cfg_x1,w_data,w_strobe);
                    16'h001c:cfg_y1<=merge_bytes(cfg_y1,w_data,w_strobe);
                    16'h0020:cfg_threshold<=merge_bytes(cfg_threshold,w_data,w_strobe);
                    16'h0024:cfg_min_run<=merge_bytes(cfg_min_run,w_data,w_strobe);
                    default:s_axi_bresp<=SLVERR;
                endcase
            end
            if(s_axi_arvalid && s_axi_arready) begin
                s_axi_rvalid<=1;s_axi_rresp<=OKAY;s_axi_rdata<=0;
                if(s_axi_araddr[1:0]!=0) s_axi_rresp<=SLVERR;
                else case(s_axi_araddr)
                    16'h0000:s_axi_rdata<=0;
                    16'h0004:s_axi_rdata<={20'b0,sticky_code,4'b0,(reset_count!=0),sticky_error,sticky_done,effective_busy};
                    16'h0008:s_axi_rdata<=32'h4c535431;
                    16'h000c:s_axi_rdata<=32'h00010000;
                    16'h0010:s_axi_rdata<=cfg_x0;
                    16'h0014:s_axi_rdata<=cfg_y0;
                    16'h0018:s_axi_rdata<=cfg_x1;
                    16'h001c:s_axi_rdata<=cfg_y1;
                    16'h0020:s_axi_rdata<=cfg_threshold;
                    16'h0024:s_axi_rdata<=cfg_min_run;
                    16'h0028:s_axi_rdata<=32'd1228800;
                    16'h002c:s_axi_rdata<=32'd2560;
                    16'h0030:s_axi_rdata<=32'd640;
                    16'h0034:s_axi_rdata<=32'd480;
                    16'h0038:s_axi_rdata<=completed_frames;
                    16'h003c:s_axi_rdata<=elapsed_cycles[31:0];
                    16'h0040:s_axi_rdata<=elapsed_cycles[63:32];
                    default:s_axi_rresp<=SLVERR;
                endcase
            end
            // Events win over a concurrent CLEAR. Explicit ABORT wins over events.
            if(running && core_reset_n && !abort_commit) begin
                if(core_error && !core_error_d) begin
                    running<=0;sticky_error<=1;sticky_code<=core_error_code;sticky_done<=0;
                end else if(core_done && !core_done_d) begin
                    running<=0;sticky_done<=1;completed_frames<=completed_frames+1;
                end
            end
        end
    end
    // AWPROT/ARPROT are accepted for AXI compliance; this peripheral does not
    // distinguish privileged/security access classes. No interrupts/auto-restart.
endmodule
