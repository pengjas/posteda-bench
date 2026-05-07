// Combinational fixed-point adder: a[N-1:0], b[N-1:0], c[N-1:0]. Default N=64.
`timescale 1ns/1ps
module equiv_tb;
    reg  [63:0] a, b;
    wire [63:0] c;
    integer i, fd, seed;
    reg [1023:0] logpath;

    fixed_point_adder dut(.a(a), .b(b), .c(c));

    initial begin
        if (!$value$plusargs("LOG=%s", logpath)) logpath = "equiv.log";
        fd = $fopen(logpath, "w");
        seed = 42;
        for (i = 0; i < 10000; i = i + 1) begin
            a = {$random(seed), $random(seed)};
            b = {$random(seed), $random(seed)};
            #1;
            $fdisplay(fd, "%0d %h", i, c);
        end
        $fclose(fd);
        $finish;
    end
endmodule
