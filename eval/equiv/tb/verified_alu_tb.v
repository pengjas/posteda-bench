// Combinational MIPS-like ALU.
// Inputs: a[31:0], b[31:0], aluc[5:0]. Outputs: r[31:0], zero, carry, negative, overflow, flag.
`timescale 1ns/1ps
module equiv_tb;
    reg  [31:0] a, b;
    reg  [5:0]  aluc;
    wire [31:0] r;
    wire        zero, carry, negative, overflow, flag;
    integer i, fd, seed;
    reg [1023:0] logpath;

    // Cycle through a list of valid opcodes so we exercise every branch,
    // not just the ~25% of 6-bit patterns that decode to a known ALU op.
    reg [5:0] ops [0:17];
    initial begin
        ops[ 0] = 6'b100000; ops[ 1] = 6'b100001; ops[ 2] = 6'b100010;
        ops[ 3] = 6'b100011; ops[ 4] = 6'b100100; ops[ 5] = 6'b100101;
        ops[ 6] = 6'b100110; ops[ 7] = 6'b100111; ops[ 8] = 6'b101010;
        ops[ 9] = 6'b101011; ops[10] = 6'b000000; ops[11] = 6'b000010;
        ops[12] = 6'b000011; ops[13] = 6'b000100; ops[14] = 6'b000110;
        ops[15] = 6'b000111; ops[16] = 6'b001000; ops[17] = 6'b001111;
    end

    verified_alu dut(.a(a), .b(b), .aluc(aluc),
                     .r(r), .zero(zero), .carry(carry),
                     .negative(negative), .overflow(overflow), .flag(flag));

    initial begin
        if (!$value$plusargs("LOG=%s", logpath)) logpath = "equiv.log";
        fd = $fopen(logpath, "w");
        seed = 42;
        #2; // let the ops initial run
        for (i = 0; i < 10000; i = i + 1) begin
            a = $random(seed);
            b = $random(seed);
            aluc = ops[i % 18];
            #1;
            // `flag` can be high-Z for non-SLT ops; log %b to capture Z/X.
            $fdisplay(fd, "%0d %h %b %b %b %b %b", i, r, zero, carry, negative, overflow, flag);
        end
        $fclose(fd);
        $finish;
    end
endmodule
