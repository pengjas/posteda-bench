// multi_16bit: clk, rst_n (async), start, ain[15:0], bin[15:0], yout[31:0], done.
// done pulses after ~16 cycles of start. Event-match on done rising.
`timescale 1ns/1ps
module equiv_tb;
    reg         clk, rst_n, start;
    reg  [15:0] ain, bin;
    wire [31:0] yout;
    wire        done;
    reg         done_d;
    integer i, fd, seed, fired;
    reg [1023:0] logpath;

    multi_16bit dut(.clk(clk), .rst_n(rst_n), .start(start),
                    .ain(ain), .bin(bin), .yout(yout), .done(done));

    always #5 clk = ~clk;

    // Capture yout on done rising (multiply completion pulse).
    always @(posedge clk) begin
        done_d <= done;
        if (rst_n && done && !done_d) begin
            $fdisplay(fd, "%0d %h", fired, yout);
            fired = fired + 1;
        end
    end

    initial begin
        if (!$value$plusargs("LOG=%s", logpath)) logpath = "equiv.log";
        fd = $fopen(logpath, "w");
        seed = 42;
        clk = 0; rst_n = 0; start = 0; ain = 0; bin = 0;
        done_d = 0; fired = 0;
        #23 rst_n = 1;

        // One multiplication per "transaction": start=1, wait for done, then start=0 briefly.
        for (i = 0; i < 200; i = i + 1) begin
            @(posedge clk);
            ain = $random(seed);
            bin = $random(seed);
            start = 1;
            // wait for done to rise
            repeat (20) begin
                @(posedge clk);
                if (done) i = i; // no-op, placeholder
            end
            start = 0;
            @(posedge clk);
        end
        repeat (50) @(posedge clk);
        $fclose(fd);
        $finish;
    end
endmodule
