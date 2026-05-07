// uart (top): wraps uart_tx + uart_rx. We don't need to send valid UART frames;
// we just need identical stimulus to produce identical outputs on both orig & mod.
// Drive every input randomly, log every output each cycle, latency sweep.
`timescale 1ns/1ps
module equiv_tb;
    reg         clk, rst;
    reg  [7:0]  s_axis_tdata;
    reg         s_axis_tvalid;
    wire        s_axis_tready;
    wire [7:0]  m_axis_tdata;
    wire        m_axis_tvalid;
    reg         m_axis_tready;
    reg         rxd;
    wire        txd;
    wire        tx_busy, rx_busy, rx_overrun_error, rx_frame_error;
    reg  [15:0] prescale;

    integer i, fd, seed, cyc;
    reg [1023:0] logpath;

    uart dut(.clk(clk), .rst(rst),
             .s_axis_tdata(s_axis_tdata), .s_axis_tvalid(s_axis_tvalid),
             .s_axis_tready(s_axis_tready),
             .m_axis_tdata(m_axis_tdata), .m_axis_tvalid(m_axis_tvalid),
             .m_axis_tready(m_axis_tready),
             .rxd(rxd), .txd(txd),
             .tx_busy(tx_busy), .rx_busy(rx_busy),
             .rx_overrun_error(rx_overrun_error), .rx_frame_error(rx_frame_error),
             .prescale(prescale));

    always #5 clk = ~clk;

    always @(posedge clk) begin
        if (!rst) begin
            $fdisplay(fd, "%0d %h %b %h %b %b %b %b %b %b", cyc,
                      m_axis_tdata, m_axis_tvalid, s_axis_tready,
                      txd, tx_busy, rx_busy, rx_overrun_error, rx_frame_error);
            cyc = cyc + 1;
        end
    end

    initial begin : timeout_guard
        #2000000 $display("TIMEOUT"); $fclose(fd); $finish;
    end

    initial begin
        if (!$value$plusargs("LOG=%s", logpath)) logpath = "equiv.log";
        fd = $fopen(logpath, "w");
        seed = 42;
        clk = 0; rst = 1;
        s_axis_tdata = 0; s_axis_tvalid = 0;
        m_axis_tready = 1; rxd = 1; prescale = 16'd4; cyc = 0;
        #23 rst = 0;

        for (i = 0; i < 5000; i = i + 1) begin
            @(posedge clk);
            s_axis_tdata  = $random(seed);
            s_axis_tvalid = $random(seed) & 1;
            m_axis_tready = $random(seed) & 1;
            rxd           = $random(seed) & 1;
        end
        $fclose(fd);
        $finish;
    end
endmodule
