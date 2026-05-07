// Combinational 32-bit CLA adder. Ports: A[32:1], B[32:1], S[32:1], C32.
`timescale 1ns/1ps
module equiv_tb;
    reg  [32:1] A, B;
    wire [32:1] S;
    wire        C32;
    integer i, fd, seed;
    reg [1023:0] logpath;

    adder32 dut(.A(A), .B(B), .S(S), .C32(C32));

    initial begin
        if (!$value$plusargs("LOG=%s", logpath)) logpath = "equiv.log";
        fd = $fopen(logpath, "w");
        seed = 42;
        for (i = 0; i < 10000; i = i + 1) begin
            A = $random(seed);
            B = $random(seed);
            #1;
            $fdisplay(fd, "%0d %h %b", i, S, C32);
        end
        $fclose(fd);
        $finish;
    end
endmodule
