`timescale 1ns/1ps
// horizontal_v4_20261019. Integer-scaled, full-frame, single-clock baseline.
// No AXI-Lite wrapper: pulse start when !busy, with configuration stable.
module laser_stripe_core (
    input wire clk, input wire rst_n, input wire start,
    input wire [9:0] cfg_x0, cfg_x1,
    input wire [8:0] cfg_y0, cfg_y1,
    input wire [15:0] cfg_threshold_q8,
    input wire [9:0] cfg_min_run,
    output wire busy, output reg done, output reg error,
    output reg [3:0] error_code,
    input wire [31:0] s_axis_tdata, input wire [3:0] s_axis_tkeep,
    input wire s_axis_tlast, input wire s_axis_tvalid,
    output wire s_axis_tready,
    output wire [31:0] m_axis_tdata, output wire [3:0] m_axis_tkeep,
    output wire m_axis_tlast, output wire m_axis_tvalid,
    input wire m_axis_tready
);
    localparam W=640, H=480, GROUPS=214, FRAME_WORDS=102720;
    typedef enum logic [4:0] {IDLE,CAPTURE,LOAD_REQ,LOAD_WAIT,INIT_SUM,
        MAKE_D,MAKE_C,CHECK_PEAK,WALK_LEFT,WALK_RIGHT,CHECK_WIDTH,
        ACCUMULATE,DIV_INIT,DIVIDE,NEXT_COLUMN,FILTER_COLUMNS,FILTER_DONE,
        RUN_SCAN,RUN_CLEAR,OUTPUT_FRAME} state_t;
    state_t state;
    reg [9:0] x0,x1,min_run;
    reg [8:0] y0,y1;
    reg [15:0] threshold_q8;
    // 3 signed 10-bit red scores per word; last group of each row padded.
    // Separate synchronous port below; no reset on frame storage.
    (* ram_style="block" *) reg [29:0] frame_mem [0:FRAME_WORDS-1];
    reg [29:0] frame_word;
    reg [19:0] pack_low;
    reg [1:0] pack_lane;
    reg [16:0] write_addr;
    reg [18:0] input_count;
    reg [9:0] input_x;
    wire signed [9:0] pixel_score =
        ($signed({2'b00,s_axis_tdata[23:16]}) <<< 1)
        - $signed({2'b00,s_axis_tdata[15:8]})
        - $signed({2'b00,s_axis_tdata[7:0]});
    wire input_ok=(s_axis_tkeep==4'hf) && (s_axis_tdata[31:24]==0)
        && (s_axis_tlast==(input_count==307199));
    wire accept=s_axis_tvalid && s_axis_tready;
    wire store_en=accept && input_ok && ((pack_lane==2)||(input_x==639));
    wire [29:0] store_word=(input_x==639) ? {20'b0,pixel_score}
                                                     : {pixel_score,pack_low};
    reg [9:0] column_x;
    reg [7:0] column_group;
    reg [1:0] column_lane;
    reg [8:0] load_y, scan_y;
    wire [16:0] read_addr=load_y*GROUPS+column_group;
    always @(posedge clk) begin
        if(store_en) frame_mem[write_addr] <= store_word;
        if(state==LOAD_REQ) frame_word <= frame_mem[read_addr];
    end
    // Small column scratchpads. Multiple asynchronous read ports are intentional.
    (* ram_style="distributed" *) reg signed [9:0] column_mem [0:H-1];
    (* ram_style="distributed" *) reg signed [15:0] d_mem [0:H-1];
    (* ram_style="distributed" *) reg signed [17:0] c_mem [0:H-1];
    reg [639:0] raw_valid, filtered_valid;
    (* ram_style="distributed" *) reg [17:0] coordinate [0:W-1];
    reg [5:0] tap;
    reg signed [31:0] background_sum;
    reg signed [31:0] peak_value;
    reg [8:0] peak_y;
    reg signed [31:0] cutoff_k;
    reg [3:0] distance_y, left_width, right_width;
    reg [8:0] weight_y, weight_end;
    reg [31:0] denominator;
    reg [63:0] numerator;
    reg [63:0] dividend, quotient;
    reg [32:0] remainder;
    reg [5:0] divide_bit;
    reg [9:0] filter_x, run_x, run_start, run_length, clear_x, clear_end, out_x;
    integer neighbors;
    reg signed [31:0] d_value,c_value;
    reg signed [31:0] weight_value;
    reg [32:0] trial_remainder;
    reg [63:0] trial_quotient;
    function automatic integer clamp_y(input integer y);
        if(y<0) clamp_y=0;
        else if(y>=H) clamp_y=H-1;
        else clamp_y=y;
    endfunction
    assign busy=(state!=IDLE);
    assign s_axis_tready=(state==CAPTURE);
    assign m_axis_tvalid=(state==OUTPUT_FRAME);
    assign m_axis_tdata=(state==OUTPUT_FRAME && filtered_valid[out_x])
        ? {1'b1,13'b0,coordinate[out_x]} : 32'b0;
    assign m_axis_tkeep=4'hf;
    assign m_axis_tlast=(state==OUTPUT_FRAME && out_x==639);

    always @* begin
        d_value=31*$signed(column_mem[scan_y])-background_sum;
        c_value=$signed(d_mem[clamp_y($signed({1'b0,scan_y})-1)])
            + 2*$signed(d_mem[scan_y])
            + $signed(d_mem[clamp_y($signed({1'b0,scan_y})+1)]);
        neighbors=0;
        for(integer j=-2;j<=2;j=j+1)
            if(($signed({1'b0,filter_x})+j>=0)&&($signed({1'b0,filter_x})+j<W))
                neighbors=neighbors+raw_valid[$signed({1'b0,filter_x})+j];
        trial_remainder={remainder[31:0],dividend[63]};
        trial_quotient={quotient[62:0],1'b0};
        if(trial_remainder>={1'b0,denominator}) begin
            trial_remainder=trial_remainder-{1'b0,denominator};
            trial_quotient[0]=1'b1;
        end
        weight_value=20*$signed(c_mem[weight_y])-cutoff_k;
    end
    always @(posedge clk) begin
        if(!rst_n) begin
            state<=IDLE;done<=0;error<=0;error_code<=0;
            raw_valid<=0;filtered_valid<=0;
            x0<=0;x1<=1;y0<=0;y1<=1;min_run<=0;threshold_q8<=0;
            input_count<=0;input_x<=0;pack_lane<=0;pack_low<=0;write_addr<=0;
            column_x<=0;column_group<=0;column_lane<=0;load_y<=0;scan_y<=0;
            tap<=0;background_sum<=0;peak_value<=0;peak_y<=0;cutoff_k<=0;
            distance_y<=0;left_width<=0;right_width<=0;weight_y<=0;weight_end<=0;
            denominator<=0;numerator<=0;dividend<=0;quotient<=0;remainder<=0;divide_bit<=0;
            filter_x<=0;run_x<=0;run_start<=0;run_length<=0;clear_x<=0;clear_end<=0;out_x<=0;
        end else case(state)
        IDLE: if(start) begin
            done<=0;error<=0;error_code<=0;
            raw_valid<=0;filtered_valid<=0;
            if(cfg_x0>=cfg_x1 || cfg_x1>640 || cfg_y0>=cfg_y1 || cfg_y1>480
                || (cfg_min_run!=0 && cfg_min_run!=8)) begin
                error<=1;error_code<=1;
            end else begin
                x0<=cfg_x0;x1<=cfg_x1;y0<=cfg_y0;y1<=cfg_y1;
                threshold_q8<=cfg_threshold_q8;min_run<=cfg_min_run;
                input_count<=0;input_x<=0;pack_lane<=0;pack_low<=0;write_addr<=0;
                state<=CAPTURE;
            end
        end
        CAPTURE: if(accept) begin
            if(!input_ok) begin
                error<=1;error_code<= (s_axis_tkeep!=4'hf || s_axis_tdata[31:24]!=0)? 2:3;
                state<=IDLE;
            end else begin
                input_count<=input_count+1;
                if(input_x==639) begin input_x<=0;pack_lane<=0;pack_low<=0;end
                else begin
                    input_x<=input_x+1;
                    if(pack_lane==2) pack_lane<=0;
                    else pack_lane<=pack_lane+1;
                    if(pack_lane==0) pack_low[9:0]<=pixel_score;
                    if(pack_lane==1) pack_low[19:10]<=pixel_score;
                end
                if(store_en) write_addr<=write_addr+1;
                if(input_count==307199) begin
                    column_x<=x0;column_group<=x0/3;column_lane<=x0%3;
                    load_y<=0;state<=LOAD_REQ;
                end
            end
        end
        LOAD_REQ: state<=LOAD_WAIT;
        LOAD_WAIT: begin
            case(column_lane)
                0:column_mem[load_y]<=$signed(frame_word[9:0]);
                1:column_mem[load_y]<=$signed(frame_word[19:10]);
                2:column_mem[load_y]<=$signed(frame_word[29:20]);
                default:column_mem[load_y]<=0;
            endcase
            if(load_y==479) begin tap<=0;background_sum<=0;scan_y<=0;state<=INIT_SUM;end
            else begin load_y<=load_y+1;state<=LOAD_REQ;end
        end
        INIT_SUM: begin
            background_sum<=background_sum+$signed(column_mem[clamp_y($signed({1'b0,tap})-15)]);
            if(tap==30) state<=MAKE_D;else tap<=tap+1;
        end
        MAKE_D: begin
            d_mem[scan_y]<=d_value[15:0];
            background_sum<=background_sum
                -$signed(column_mem[clamp_y($signed({1'b0,scan_y})-15)])
                +$signed(column_mem[clamp_y($signed({1'b0,scan_y})+16)]);
            if(scan_y==479) begin scan_y<=0;peak_value<=-131072;peak_y<=0;state<=MAKE_C;end
            else scan_y<=scan_y+1;
        end
        MAKE_C: begin
            c_mem[scan_y]<=c_value[17:0];
            if(scan_y>=y0 && scan_y<y1 && c_value>peak_value) begin
                peak_value<=c_value;peak_y<=scan_y;
            end
            if(scan_y==479) state<=CHECK_PEAK;else scan_y<=scan_y+1;
        end
        CHECK_PEAK: begin
            if(peak_y<=y0 || peak_y>=y1-1 || peak_value*256 < $signed({1'b0,threshold_q8})*248)
                state<=NEXT_COLUMN;
            else begin
                cutoff_k<=(9*peak_value>7440)?9*peak_value:7440;
                left_width<=0;right_width<=0;distance_y<=1;state<=WALK_LEFT;
            end
        end
        WALK_LEFT: begin
            if($signed({1'b0,peak_y})-$signed({1'b0,distance_y}) < $signed({1'b0,y0})) begin
                distance_y<=1;state<=WALK_RIGHT;
            end else if(20*$signed(c_mem[peak_y-distance_y])<cutoff_k) begin
                distance_y<=1;state<=WALK_RIGHT;
            end else begin
                left_width<=distance_y;
                if(distance_y==8) begin distance_y<=1;state<=WALK_RIGHT;end
                else distance_y<=distance_y+1;
            end
        end
        WALK_RIGHT: begin
            if(peak_y+distance_y>=y1) state<=CHECK_WIDTH;
            else if(20*$signed(c_mem[peak_y+distance_y])<cutoff_k) state<=CHECK_WIDTH;
            else begin
                right_width<=distance_y;
                if(distance_y==8) state<=CHECK_WIDTH;else distance_y<=distance_y+1;
            end
        end
        CHECK_WIDTH: begin
            if(left_width+right_width+1<2 || left_width+right_width+1>14) state<=NEXT_COLUMN;
            else begin
                weight_y<=peak_y-left_width;weight_end<=peak_y+right_width;
                denominator<=0;numerator<=0;state<=ACCUMULATE;
            end
        end
        ACCUMULATE: begin
            if(weight_value>0) begin
                denominator<=denominator+$unsigned(weight_value);
                numerator<=numerator+({32'b0,$unsigned(weight_value)}*weight_y);
            end
            if(weight_y==weight_end) state<=DIV_INIT;
            else weight_y<=weight_y+1;
            // Divider initialization occurs on the following cycle.
            divide_bit<=0;quotient<=0;remainder<=0;
        end
        DIV_INIT: begin
            if(denominator==0) state<=NEXT_COLUMN;
            else begin
                dividend<=(numerator<<8)+(denominator>>1);
                divide_bit<=0;quotient<=0;remainder<=0;state<=DIVIDE;
            end
        end
        DIVIDE: begin
            dividend<={dividend[62:0],1'b0};
            quotient<=trial_quotient;remainder<=trial_remainder;
            if(divide_bit==63) begin
                coordinate[column_x]<=trial_quotient[17:0];raw_valid[column_x]<=1;
                state<=NEXT_COLUMN;
            end else divide_bit<=divide_bit+1;
        end
        NEXT_COLUMN: begin
            if(column_x==x1-1) begin filter_x<=0;state<=FILTER_COLUMNS;end
            else begin
                column_x<=column_x+1;
                if(column_lane==2) begin column_lane<=0;column_group<=column_group+1;end
                else column_lane<=column_lane+1;
                load_y<=0;state<=LOAD_REQ;
            end
        end
        FILTER_COLUMNS: begin
            filtered_valid[filter_x]<=raw_valid[filter_x] && neighbors>=3;
            if(filter_x==639) state<=FILTER_DONE;else filter_x<=filter_x+1;
        end
        FILTER_DONE: begin
            out_x<=0;run_x<=0;run_length<=0;
            if(min_run==0) state<=OUTPUT_FRAME;else state<=RUN_SCAN;
        end
        RUN_SCAN: begin
            if(run_x>640) begin out_x<=0;state<=OUTPUT_FRAME;end
            else if(run_x<640 && filtered_valid[run_x]) begin
                if(run_length==0) run_start<=run_x;
                run_length<=run_length+1;run_x<=run_x+1;
            end else begin
                run_x<=run_x+1;run_length<=0;
                if(run_length>0 && run_length<min_run) begin
                    clear_x<=run_start;clear_end<=run_start+run_length-1;state<=RUN_CLEAR;
                end
            end
        end
        RUN_CLEAR: begin
            filtered_valid[clear_x]<=0;
            if(clear_x==clear_end) state<=RUN_SCAN;else clear_x<=clear_x+1;
        end
        OUTPUT_FRAME: if(m_axis_tready) begin
            if(out_x==639) begin done<=1;state<=IDLE;end
            else out_x<=out_x+1;
        end
        default: begin state<=IDLE;error<=1;error_code<=4;end
        endcase
    end
endmodule
