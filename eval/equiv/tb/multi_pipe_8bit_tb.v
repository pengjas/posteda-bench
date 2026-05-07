// multi_pipe_8bit: clk, rst_n (async), mul_en_in, mul_a[7:0], mul_b[7:0], mul_en_out, mul_out[15:0].
`timescale 1ns/1ps
module equiv_tb;
    reg        clk, rst_n, mul_en_in;
    reg  [7:0] mul_a, mul_b;
    wire       mul_en_out;
    wire [15:0] mul_out;
    integer i, fd, seed, fired;
    reg [1023:0] logpath;

    multi_pipe_8bit dut(.clk(clk), .rst_n(rst_n),
                        .mul_en_in(mul_en_in), .mul_a(mul_a), .mul_b(mul_b),
                        .mul_en_out(mul_en_out), .mul_out(mul_out));

    always #5 clk = ~clk;

    always @(posedge clk) begin
        if (rst_n && mul_en_out) begin
            $fdisplay(fd, "%0d %h", fired, mul_out);
            fired = fired + 1;
        end
    end

    initial begin
        if (!$value$plusargs("LOG=%s", logpath)) logpath = "equiv.log";
        fd = $fopen(logpath, "w");
        seed = 42;
        clk = 0;
        rst_n = 0;
        mul_en_in = 0;
        mul_a = 0;
        mul_b = 0;
        fired = 0;
        #23 rst_n = 1;

        for (i = 0; i < 1000; i = i + 1) begin
            @(posedge clk);
            mul_a = $random(seed);
            mul_b = $random(seed);
            mul_en_in = 1;
        end
        @(posedge clk);
        mul_en_in = 0;
        repeat (200) @(posedge clk);
        $fclose(fd);
        $finish;
    end
endmodule
