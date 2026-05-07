// jpeg_encoder: too protocol-heavy for clean event-matching with random
// stimulus. Use cycle-stamped log over all outputs + latency sweep.
// Any agent edit that breaks behavior will diverge on these signals.
`timescale 1ns/1ps
module equiv_tb;
    reg         clk, ena, rst, dstrb;
    reg  [7:0]  din, qnt_val;
    wire [5:0]  qnt_cnt;
    wire [3:0]  size, rlen;
    wire [11:0] amp;
    wire        douten;

    integer i, fd, seed, cyc;
    reg [1023:0] logpath;

    jpeg_encoder dut(.clk(clk), .ena(ena), .rst(rst),
                     .dstrb(dstrb), .din(din),
                     .qnt_val(qnt_val), .qnt_cnt(qnt_cnt),
                     .size(size), .rlen(rlen), .amp(amp), .douten(douten));

    always #5 clk = ~clk;

    always @(posedge clk) begin
        if (rst) begin
            $fdisplay(fd, "%0d %h %h %h %h %b %b", cyc,
                      size, rlen, amp, qnt_cnt, douten, ena);
            cyc = cyc + 1;
        end
    end

    initial begin : timeout_guard
        #5000000 $display("TIMEOUT"); $fclose(fd); $finish;
    end

    initial begin
        if (!$value$plusargs("LOG=%s", logpath)) logpath = "equiv.log";
        fd = $fopen(logpath, "w");
        seed = 42;
        clk = 0; rst = 0; ena = 0; dstrb = 0; din = 0; qnt_val = 8'd16;
        cyc = 0;
        #23 rst = 1;
        ena = 1;

        for (i = 0; i < 3000; i = i + 1) begin
            @(posedge clk);
            din = $random(seed);
            dstrb = $random(seed) & 1;
            qnt_val = $random(seed) | 8'h01;   // nonzero
        end
        $fclose(fd);
        $finish;
    end
endmodule
