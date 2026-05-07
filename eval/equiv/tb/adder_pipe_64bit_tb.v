// adder_pipe_64bit: clk, rst_n (async), i_en, adda[63:0], addb[63:0], result[64:0], o_en.
// Event-match on o_en rising: log result.
`timescale 1ns/1ps
module equiv_tb;
    reg         clk, rst_n, i_en;
    reg  [63:0] adda, addb;
    wire [64:0] result;
    wire        o_en;
    integer i, fd, seed, fired;
    reg [1023:0] logpath;

    adder_pipe_64bit dut(.clk(clk), .rst_n(rst_n), .i_en(i_en),
                         .adda(adda), .addb(addb),
                         .result(result), .o_en(o_en));

    always #5 clk = ~clk;

    always @(posedge clk) begin
        if (rst_n && o_en) begin
            $fdisplay(fd, "%0d %h", fired, result);
            fired = fired + 1;
        end
    end

    initial begin
        if (!$value$plusargs("LOG=%s", logpath)) logpath = "equiv.log";
        fd = $fopen(logpath, "w");
        seed = 42;
        clk = 0;
        rst_n = 0;
        i_en = 0;
        adda = 0;
        addb = 0;
        fired = 0;
        #23 rst_n = 1;

        for (i = 0; i < 1000; i = i + 1) begin
            @(posedge clk);
            adda = {$random(seed), $random(seed)};
            addb = {$random(seed), $random(seed)};
            i_en = 1;
        end
        @(posedge clk);
        i_en = 0;

        // Drain pipeline
        repeat (200) @(posedge clk);
        $fclose(fd);
        $finish;
    end
endmodule
