// Combinational. Apply N random (A,B) vectors; log result + overflow.
`timescale 1ns/1ps
module equiv_tb;
    reg  [63:0] A, B;
    wire [63:0] result;
    wire        overflow;
    integer i, fd, seed;
    reg [1023:0] logpath;

    sub_64bit dut(.A(A), .B(B), .result(result), .overflow(overflow));

    initial begin
        if (!$value$plusargs("LOG=%s", logpath)) logpath = "equiv.log";
        fd = $fopen(logpath, "w");
        seed = 42;
        for (i = 0; i < 10000; i = i + 1) begin
            A = {$random(seed), $random(seed)};
            B = {$random(seed), $random(seed)};
            #1;
            $fdisplay(fd, "%0d %h %b", i, result, overflow);
        end
        $fclose(fd);
        $finish;
    end
endmodule
