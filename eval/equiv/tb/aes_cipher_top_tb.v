// aes_cipher_top: clk, rst (active-low), ld (load key+plaintext), done,
// key[127:0], text_in[127:0], text_out[127:0].
// Event-match on done rising: log text_out.
`timescale 1ns/1ps
module equiv_tb;
    reg          clk, rst, ld;
    reg  [127:0] key, text_in;
    wire         done;
    wire [127:0] text_out;
    reg          done_d;
    integer i, j, fd, seed, fired;
    reg [1023:0] logpath;

    aes_cipher_top dut(.clk(clk), .rst(rst), .ld(ld), .done(done),
                       .key(key), .text_in(text_in), .text_out(text_out));

    always #5 clk = ~clk;

    always @(posedge clk) begin
        done_d <= done;
        if (rst && done && !done_d) begin
            $fdisplay(fd, "%0d %h", fired, text_out);
            fired = fired + 1;
        end
    end

    initial begin : timeout_guard
        #2000000 $display("TIMEOUT"); $fclose(fd); $finish;
    end

    initial begin
        if (!$value$plusargs("LOG=%s", logpath)) logpath = "equiv.log";
        fd = $fopen(logpath, "w");
        seed = 42;
        clk = 0; rst = 0; ld = 0; key = 0; text_in = 0;
        done_d = 0; fired = 0;
        #23 rst = 1;

        for (i = 0; i < 50; i = i + 1) begin : txn
            integer wc;
            for (j = 0; j < 4; j = j + 1) key[32*j +: 32] = $random(seed);
            for (j = 0; j < 4; j = j + 1) text_in[32*j +: 32] = $random(seed);
            @(posedge clk);
            ld = 1;
            @(posedge clk);
            ld = 0;
            // Wait for done (cipher runs ~10-12 rounds)
            wc = 0;
            while (!done && wc < 200) begin
                @(posedge clk);
                wc = wc + 1;
            end
            // wait for done to drop before next txn
            wc = 0;
            while (done && wc < 50) begin
                @(posedge clk);
                wc = wc + 1;
            end
        end
        repeat (100) @(posedge clk);
        $fclose(fd);
        $finish;
    end
endmodule
