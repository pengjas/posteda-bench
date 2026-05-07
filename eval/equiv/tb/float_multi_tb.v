// float_multi: clk, rst (active-high async), a[31:0], b[31:0], z[31:0].
// No enable signal. Hold (a,b) stable for 40 cycles (well past any plausible
// pipeline depth the agent would add), then sample z as one transaction.
`timescale 1ns/1ps
module equiv_tb;
    reg         clk, rst;
    reg  [31:0] a, b;
    wire [31:0] z;
    integer i, fd, seed, fired;
    reg [1023:0] logpath;

    float_multi dut(.clk(clk), .rst(rst), .a(a), .b(b), .z(z));

    always #5 clk = ~clk;

    initial begin : timeout_guard
        #2000000 $display("TIMEOUT"); $fclose(fd); $finish;
    end

    initial begin
        if (!$value$plusargs("LOG=%s", logpath)) logpath = "equiv.log";
        fd = $fopen(logpath, "w");
        seed = 42;
        clk = 0; rst = 1; a = 0; b = 0; fired = 0;
        #23 rst = 0;

        for (i = 0; i < 100; i = i + 1) begin
            a = $random(seed);
            b = $random(seed);
            repeat (40) @(posedge clk);   // >= any plausible pipeline depth
            $fdisplay(fd, "%0d %h", fired, z);
            fired = fired + 1;
        end
        $fclose(fd);
        $finish;
    end
endmodule
