// gcd: clk, reset (sync), req_msg[31:0]/req_val/req_rdy, resp_msg[15:0]/resp_val/resp_rdy.
// Event-match on (resp_val && resp_rdy) — pipeline depth changes do not affect the
// logged sequence of output messages.
`timescale 1ns/1ps
module equiv_tb;
    reg         clk, reset;
    reg  [31:0] req_msg;
    reg         req_val;
    wire        req_rdy;
    wire [15:0] resp_msg;
    wire        resp_val;
    reg         resp_rdy;

    integer i, fd, seed, fired;
    reg [1023:0] logpath;

    gcd dut(.clk(clk), .reset(reset),
            .req_msg(req_msg), .req_val(req_val), .req_rdy(req_rdy),
            .resp_msg(resp_msg), .resp_val(resp_val), .resp_rdy(resp_rdy));

    // clock
    always #5 clk = ~clk;

    // Log every (resp_val && resp_rdy) firing with the transaction index.
    // `fired` is monotonic across both orig and mod runs because stimulus is
    // identical — so plain line-by-line diff works.
    always @(posedge clk) begin
        if (!reset && resp_val && resp_rdy) begin
            $fdisplay(fd, "%0d %h", fired, resp_msg);
            fired = fired + 1;
        end
    end

    initial begin
        if (!$value$plusargs("LOG=%s", logpath)) logpath = "equiv.log";
        fd = $fopen(logpath, "w");
        seed = 42;
        clk = 0;
        reset = 1;
        req_msg = 0;
        req_val = 0;
        resp_rdy = 1;   // always ready to drain
        fired = 0;
        #23 reset = 0;

        for (i = 0; i < 500; i = i + 1) begin
            // Wait until DUT can accept a request
            @(posedge clk);
            while (!req_rdy) @(posedge clk);
            req_msg = {$random(seed)};
            // Avoid b=0 which causes GCD to degenerate; force low bits of b non-zero
            if (req_msg[15:0] == 16'h0000) req_msg[15:0] = 16'h0001;
            req_val = 1;
            @(posedge clk);
            req_val = 0;
        end

        // Drain: wait a while for last responses to appear
        repeat (500) @(posedge clk);
        $fclose(fd);
        $finish;
    end
endmodule
