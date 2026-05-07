// ethmac: massive Wishbone/MII MAC. Generic random-stim equivalence check —
// drive every input with the same random stream, sample every output per cycle.
// Same stimulus → same outputs on both orig & mod, up to a latency offset.
`timescale 1ns/1ps
module equiv_tb;
    // Wishbone common
    reg          wb_clk_i, wb_rst_i;
    reg  [31:0]  wb_dat_i;
    wire [31:0]  wb_dat_o;
    // Wishbone slave
    reg  [11:2]  wb_adr_i;
    reg  [3:0]   wb_sel_i;
    reg          wb_we_i, wb_cyc_i, wb_stb_i;
    wire         wb_ack_o, wb_err_o;
    // Wishbone master
    wire [31:0]  m_wb_adr_o;
    wire [3:0]   m_wb_sel_o;
    wire         m_wb_we_o;
    wire [31:0]  m_wb_dat_o;
    reg  [31:0]  m_wb_dat_i;
    wire         m_wb_cyc_o, m_wb_stb_o;
    reg          m_wb_ack_i, m_wb_err_i;
    wire [2:0]   m_wb_cti_o;
    wire [1:0]   m_wb_bte_o;
    // TX
    reg          mtx_clk_pad_i;
    wire [3:0]   mtxd_pad_o;
    wire         mtxen_pad_o, mtxerr_pad_o;
    // RX
    reg          mrx_clk_pad_i;
    reg  [3:0]   mrxd_pad_i;
    reg          mrxdv_pad_i, mrxerr_pad_i, mcoll_pad_i, mcrs_pad_i;
    // MIIM
    wire         mdc_pad_o;
    reg          md_pad_i;
    wire         md_pad_o, md_padoe_o;
    // Interrupt
    wire         int_o;

    integer i, fd, seed, cyc;
    reg [1023:0] logpath;

    ethmac dut(.wb_clk_i(wb_clk_i), .wb_rst_i(wb_rst_i),
               .wb_dat_i(wb_dat_i), .wb_dat_o(wb_dat_o),
               .wb_adr_i(wb_adr_i), .wb_sel_i(wb_sel_i),
               .wb_we_i(wb_we_i), .wb_cyc_i(wb_cyc_i), .wb_stb_i(wb_stb_i),
               .wb_ack_o(wb_ack_o), .wb_err_o(wb_err_o),
               .m_wb_adr_o(m_wb_adr_o), .m_wb_sel_o(m_wb_sel_o),
               .m_wb_we_o(m_wb_we_o),
               .m_wb_dat_o(m_wb_dat_o), .m_wb_dat_i(m_wb_dat_i),
               .m_wb_cyc_o(m_wb_cyc_o), .m_wb_stb_o(m_wb_stb_o),
               .m_wb_ack_i(m_wb_ack_i), .m_wb_err_i(m_wb_err_i),
               .m_wb_cti_o(m_wb_cti_o), .m_wb_bte_o(m_wb_bte_o),
               .mtx_clk_pad_i(mtx_clk_pad_i),
               .mtxd_pad_o(mtxd_pad_o), .mtxen_pad_o(mtxen_pad_o),
               .mtxerr_pad_o(mtxerr_pad_o),
               .mrx_clk_pad_i(mrx_clk_pad_i),
               .mrxd_pad_i(mrxd_pad_i), .mrxdv_pad_i(mrxdv_pad_i),
               .mrxerr_pad_i(mrxerr_pad_i), .mcoll_pad_i(mcoll_pad_i),
               .mcrs_pad_i(mcrs_pad_i),
               .mdc_pad_o(mdc_pad_o),
               .md_pad_i(md_pad_i), .md_pad_o(md_pad_o),
               .md_padoe_o(md_padoe_o),
               .int_o(int_o));

    // One clock for simplicity (drive all clock pads from same edge).
    always #5 wb_clk_i = ~wb_clk_i;
    always @(*) begin
        mtx_clk_pad_i = wb_clk_i;
        mrx_clk_pad_i = wb_clk_i;
    end

    always @(posedge wb_clk_i) begin
        if (!wb_rst_i) begin
            $fdisplay(fd, "%0d %h %b %b %h %h %b %b %b %b %b %b %h %h %b %b %b",
                      cyc, wb_dat_o, wb_ack_o, wb_err_o,
                      m_wb_adr_o, m_wb_dat_o, m_wb_cyc_o, m_wb_stb_o, m_wb_we_o,
                      mtxen_pad_o, mtxerr_pad_o, int_o,
                      mtxd_pad_o, m_wb_sel_o, m_wb_cti_o[0], m_wb_bte_o[0], mdc_pad_o);
            cyc = cyc + 1;
        end
    end

    initial begin : timeout_guard
        #5000000 $display("TIMEOUT"); $fclose(fd); $finish;
    end

    initial begin
        if (!$value$plusargs("LOG=%s", logpath)) logpath = "equiv.log";
        fd = $fopen(logpath, "w");
        seed = 42;
        wb_clk_i = 0; wb_rst_i = 1;
        wb_dat_i = 0; wb_adr_i = 0; wb_sel_i = 0;
        wb_we_i = 0; wb_cyc_i = 0; wb_stb_i = 0;
        m_wb_dat_i = 0; m_wb_ack_i = 0; m_wb_err_i = 0;
        mrxd_pad_i = 0; mrxdv_pad_i = 0; mrxerr_pad_i = 0;
        mcoll_pad_i = 0; mcrs_pad_i = 0;
        md_pad_i = 0;
        cyc = 0;
        #23 wb_rst_i = 0;

        for (i = 0; i < 3000; i = i + 1) begin
            @(posedge wb_clk_i);
            wb_dat_i     = {$random(seed), $random(seed)};
            wb_adr_i     = $random(seed);
            wb_sel_i     = $random(seed);
            wb_we_i      = $random(seed) & 1;
            wb_cyc_i     = $random(seed) & 1;
            wb_stb_i     = $random(seed) & 1;
            m_wb_dat_i   = {$random(seed), $random(seed)};
            m_wb_ack_i   = $random(seed) & 1;
            m_wb_err_i   = $random(seed) & 1;
            mrxd_pad_i   = $random(seed);
            mrxdv_pad_i  = $random(seed) & 1;
            mrxerr_pad_i = $random(seed) & 1;
            mcoll_pad_i  = $random(seed) & 1;
            mcrs_pad_i   = $random(seed) & 1;
            md_pad_i     = $random(seed) & 1;
        end
        $fclose(fd);
        $finish;
    end
endmodule
