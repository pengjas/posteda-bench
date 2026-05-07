// fsm: IN (1 bit), MATCH (1 bit), CLK, RST (active-high async).
// Pipeline changes could shift MATCH relative to IN in time. Use cycle-stamped
// log + latency sweep.
`timescale 1ns/1ps
module equiv_tb;
    reg  CLK, RST, IN;
    wire MATCH;
    integer i, fd, seed, cyc;
    reg [1023:0] logpath;

    fsm dut(.IN(IN), .MATCH(MATCH), .CLK(CLK), .RST(RST));

    always #5 CLK = ~CLK;

    always @(posedge CLK) begin
        if (!RST) begin
            $fdisplay(fd, "%0d %b", cyc, MATCH);
            cyc = cyc + 1;
        end
    end

    initial begin : timeout_guard
        #1000000 $display("TIMEOUT"); $fclose(fd); $finish;
    end

    initial begin
        if (!$value$plusargs("LOG=%s", logpath)) logpath = "equiv.log";
        fd = $fopen(logpath, "w");
        seed = 42;
        CLK = 0; RST = 1; IN = 0; cyc = 0;
        #23 RST = 0;

        for (i = 0; i < 5000; i = i + 1) begin
            @(posedge CLK);
            IN = $random(seed) & 1;
        end
        $fclose(fd);
        $finish;
    end
endmodule
