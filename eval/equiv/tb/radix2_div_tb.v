// radix2_div: clk, rst (sync, active-high), dividend[7:0], divisor[7:0], sign,
// opn_valid, res_valid (out), res_ready, result[15:0].
// Event-match on (res_valid && res_ready).
`timescale 1ns/1ps
module equiv_tb;
    reg        clk, rst;
    reg  [7:0] dividend, divisor;
    reg        sign, opn_valid;
    wire       res_valid;
    reg        res_ready;
    wire [15:0] result;
    integer i, fd, seed, fired;
    reg [1023:0] logpath;

    radix2_div dut(.clk(clk), .rst(rst),
                   .dividend(dividend), .divisor(divisor), .sign(sign),
                   .opn_valid(opn_valid),
                   .res_valid(res_valid), .res_ready(res_ready),
                   .result(result));

    always #5 clk = ~clk;

    always @(posedge clk) begin
        if (!rst && res_valid && res_ready) begin
            $fdisplay(fd, "%0d %h", fired, result);
            fired = fired + 1;
        end
    end

    initial begin
        if (!$value$plusargs("LOG=%s", logpath)) logpath = "equiv.log";
        fd = $fopen(logpath, "w");
        seed = 42;
        clk = 0; rst = 1; dividend = 0; divisor = 0; sign = 0;
        opn_valid = 0; res_ready = 1; fired = 0;
        #23 rst = 0;

        for (i = 0; i < 200; i = i + 1) begin : txn_loop
            integer wait_cycles;
            @(posedge clk);
            dividend = $random(seed);
            divisor = $random(seed);
            if (divisor == 0) divisor = 8'h01;
            sign = $random(seed) & 1;
            opn_valid = 1;
            @(posedge clk);
            opn_valid = 0;
            // wait for result with timeout
            wait_cycles = 0;
            while (!res_valid && wait_cycles < 100) begin
                @(posedge clk);
                wait_cycles = wait_cycles + 1;
            end
            if (!res_valid) begin
                $display("ERROR: res_valid never fired at txn %0d", i);
                $fclose(fd);
                $finish;
            end
            @(posedge clk);
        end
        repeat (50) @(posedge clk);
        $fclose(fd);
        $finish;
    end
endmodule
