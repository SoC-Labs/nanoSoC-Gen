//-----------------------------------------------------------------------------
// nanosoc_multicore_vivado_wrapper — FPGA Vivado IP wrapper for nanosoc_multicore_soc
//
// Copyright 2021-2026 SoC Labs (www.soclabs.org), University of Southampton
// SPDX-License-Identifier: Apache-2.0
//-----------------------------------------------------------------------------
// AUTO-GENERATED FILE - DO NOT EDIT DIRECTLY.
// Template:  fpga_vivado_wrapper.v.j2
// Generated: 2026-07-09 20:10:30
//-----------------------------------------------------------------------------
// Synthesizable wrapper presenting nanosoc_multicore_soc with IP-Integrator-friendly
// board interfaces.  Bonded boundary signals are wired straight to wrapper
// ports; bidir/tristate pads use the Vivado IOBUF convention (_i/_o/_t, where
// _t = 1 => Hi-Z; _t = ~oe for an active-high inner output-enable).  No pad
// cells are instantiated (the FPGA fabric provides the IOBUFs).
//
// The model-derivable topology (bonded / tied / open + oe polarity) comes from
// the chip-boundary spec; the bespoke FPGA/board layer (param overrides, board
// pin renames, debug taps, dropped pads, the SYSRESETREQ self-loop, etc.) comes
// from the FPGA profile.  Bespoke items applied:
//   - param_overrides: ETH_IMEM_MEM_FPGA_IMG="image.hex", CC_IMEM_MEM_FPGA_IMG="image.hex", CC_IMEM_RAM_ADDR_W=14, CC_DMEM_RAM_ADDR_W=12, CC_BOOTROM_ADDR_W=11
//   - port_renames: sys_sysresetn->nrst, chip_core_uart_rxd->cpu1_uart_rxd, chip_core_uart_txd->cpu1_uart_txd, rmii_rxd->phy_rmii_rxd, rmii_crs_dv->phy_rmii_crs_dv, rmii_txd->phy_rmii_txd, rmii_tx_en->phy_rmii_tx_en, mdc_pad_o->phy_mdc, qspi_csn->qspi_ncs, dap_swclktck->swd_clk, md_pad_i->mdio, dap_swditms->swd_dio, qspi_io_i->qspi_io
//   - exclude_pads: hostio4, rtc_clk, jtag_tdi, jtag_ntrst, jtag_tdo, sys_scanenable, sys_testmode
//   - debug_taps: eth_irq<-eth_irq, cpu1_wdog_reset<-chip_core_wdog_reset, sys_hresetn_o<-sys_hresetn, sys_hclk_dbg<-sys_hclk, uart_txd_dbg<-uart_txd, cpu_0_lockup_dbg<-network_core_lockup, cpu_1_lockup_dbg<-chip_core_lockup, ptp_lock_dbg<-ha1588_servo_locked
//   - feature: sysresetreq_selfloop
//   - feature: rtc_from_sys_fclk
//   - feature: dap_npotrst_from_nrst
//-----------------------------------------------------------------------------

`timescale 1ns/1ps

module nanosoc_multicore_vivado_wrapper #(
    parameter ETH_IMEM_MEM_FPGA_IMG  = "image.hex",   // $readmemh preload path
    parameter CC_IMEM_MEM_FPGA_IMG   = "image.hex",   // $readmemh preload path
    parameter CC_IMEM_RAM_ADDR_W     = 14,   // 4 * 2^14 = 64 KB
    parameter CC_DMEM_RAM_ADDR_W     = 12,   // 4 * 2^12 = 16 KB
    parameter CC_BOOTROM_ADDR_W      = 11   // 4 * 2^11 =  8 KB
) (
    input   wire          sys_fclk,  // clk
    input   wire          rmii_ref_clk,  // clk
    input   wire          nrst,  // reset
    input   wire          swd_clk,  // swd
    input   wire          swd_dio_i,  // swd (i)
    output  wire          swd_dio_o,  // swd (o)
    output  wire          swd_dio_t,  // swd (t)
    input   wire          uart_rxd,  // uart
    output  wire          uart_txd,  // uart
    input   wire          cpu1_uart_rxd,  // uart
    output  wire          cpu1_uart_txd,  // uart
    output  wire [1:0]    phy_rmii_txd,  // eth_rmii
    output  wire          phy_rmii_tx_en,  // eth_rmii
    input   wire [1:0]    phy_rmii_rxd,  // eth_rmii
    input   wire          phy_rmii_crs_dv,  // eth_rmii
    output  wire          phy_mdc,  // eth_mdio
    input   wire          mdio_i,  // eth_mdio (i)
    output  wire          mdio_o,  // eth_mdio (o)
    output  wire          mdio_t,  // eth_mdio (t)
    output  wire          qspi_sclk,  // qspi
    output  wire          qspi_ncs,  // qspi
    input   wire [3:0]    qspi_io_i,  // qspi (i)
    output  wire [3:0]    qspi_io_o,  // qspi (o)
    output  wire [3:0]    qspi_io_t,  // qspi (t)
    output  wire          spi_sclk,  // spi
    output  wire          spi_mosi,  // spi
    input   wire          spi_miso,  // spi
    output  wire [2:0]    spi_ss,  // spi
    output  wire          phc_pps_out,  // pps
    output  wire          eth_irq,  // debug (tap)
    output  wire          cpu1_wdog_reset,  // debug (tap)
    output  wire          sys_hresetn_o,  // debug (tap)
    output  wire          sys_hclk_dbg,  // debug (tap)
    output  wire          uart_txd_dbg,  // debug (tap)
    output  wire          cpu_0_lockup_dbg,  // debug (tap)
    output  wire          cpu_1_lockup_dbg,  // debug (tap)
    output  wire          ptp_lock_dbg  // debug (tap)
);

    //=========================================================================
    // Internal nets (inner-SoC out/oe legs + status taps)
    //=========================================================================
    wire          dap_swdo;
    wire          dap_swdoen;
    wire          md_pad_o;
    wire          md_padoe_o;
    wire [3:0]    qspi_io_e;
    wire          network_core_sysresetreq;
    wire          chip_core_sysresetreq;
    wire          sys_hresetn;
    wire          sys_hclk;

    //=========================================================================
    // Tristate adapters (Vivado IOBUF convention: _t = 1 -> Hi-Z)
    //=========================================================================
    assign swd_dio_o = dap_swdo;
    assign swd_dio_t = ~dap_swdoen;
    assign mdio_o = md_pad_o;
    assign mdio_t = ~md_padoe_o;
    assign qspi_io_t = ~qspi_io_e;

    //=========================================================================
    // Status / debug routing
    //=========================================================================
    assign sys_hresetn_o = sys_hresetn;  // system HRESETn status export
    assign sys_hclk_dbg = sys_hclk;  // system HCLK heartbeat LED
    assign uart_txd_dbg = uart_txd;  // CPU0 UART activity tap (-> EMIO UART1)

    //=========================================================================
    // SYSRESETREQ per-core isolation (Round-8 HW bug #1 fix)
    //
    // Each core's SYSRESETREQ (SCB->AIRCR / SWD `reset`) is routed INSIDE the
    // SoC to its dedicated nanosoc_reset_ctrl per-core input, which asserts only
    // that core's resetn (per-core reset). The wrapper does NOT loop SYSRESETREQ
    // back into the inner SoC's GLOBAL sys_sysresetreq — doing so rebooted BOTH
    // cores + the shared fabric (PHC / IPC / ring) on a single core's reset.
    // sys_sysresetreq is reserved for a genuine EXTERNAL system reset; this board
    // has no such source, so it stays tied inactive (boundary tie 1'b0). The two
    // cpu_*_sysresetreq outputs stay observed (collected into a harmless unused
    // sink) so they do not dangle.
    //=========================================================================
    wire _unused_sysresetreq = &{1'b0, network_core_sysresetreq, chip_core_sysresetreq};

    //=========================================================================
    // u_d2d_idle_slave (nanosoc_d2d_idle_slave) — terminator
    //
    // An inner bus port with nothing attached on this FPGA target. A constant
    // tie cannot express a bus response, and leaving the port UNCONNECTED is
    // worse still: Vivado ties an unconnected input to 0, so a dropped `hready`
    // wedges the bus matrix rather than merely floating. This helper answers.
    //=========================================================================
    // D2D outbound window (0x2E000000..0x2FFFFFFF) — no link on this chip; answer AHB ERROR.
    wire [1:0]    d2d_ahb_m_htrans;
    wire [31:0]   d2d_ahb_m_hrdata;
    wire          d2d_ahb_m_hready;
    wire          d2d_ahb_m_hresp;

    nanosoc_d2d_idle_slave #(
        .DATA_W         (32)
    ) u_d2d_idle_slave (
        .htrans               (d2d_ahb_m_htrans),
        .hrdata               (d2d_ahb_m_hrdata),
        .hready               (d2d_ahb_m_hready),
        .hresp                (d2d_ahb_m_hresp),
        .hclk                 (sys_fclk),
        .hresetn              (nrst)
    );

    //=========================================================================
    // nanosoc_multicore_soc — inner SoC top
    //   bonded -> wrapper net ; tied -> constant ; open -> unconnected ;
    //   tap/feature -> bespoke FPGA glue ; terminated -> terminator wire
    //=========================================================================
    nanosoc_multicore_soc #(
        .ETH_IMEM_MEM_FPGA_IMG  (ETH_IMEM_MEM_FPGA_IMG),
        .CC_IMEM_MEM_FPGA_IMG   (CC_IMEM_MEM_FPGA_IMG),
        .CC_IMEM_RAM_ADDR_W     (CC_IMEM_RAM_ADDR_W),
        .CC_DMEM_RAM_ADDR_W     (CC_DMEM_RAM_ADDR_W),
        .CC_BOOTROM_ADDR_W      (CC_BOOTROM_ADDR_W)
    ) u_soc (
        .sys_fclk             (sys_fclk),
        .rmii_ref_clk         (rmii_ref_clk),
        .sys_sysresetn        (nrst),
        .dap_swclktck         (swd_clk),
        .dap_swditms          (swd_dio_i),
        .dap_swdo             (dap_swdo),
        .dap_swdoen           (dap_swdoen),
        .uart_rxd             (uart_rxd),
        .uart_txd             (uart_txd),
        .chip_core_uart_rxd   (cpu1_uart_rxd),
        .chip_core_uart_txd   (cpu1_uart_txd),
        .rmii_txd             (phy_rmii_txd),
        .rmii_tx_en           (phy_rmii_tx_en),
        .rmii_rxd             (phy_rmii_rxd),
        .rmii_crs_dv          (phy_rmii_crs_dv),
        .mdc_pad_o            (phy_mdc),
        .md_pad_i             (mdio_i),
        .md_pad_o             (md_pad_o),
        .md_padoe_o           (md_padoe_o),
        .qspi_sclk            (qspi_sclk),
        .qspi_csn             (qspi_ncs),
        .qspi_io_i            (qspi_io_i),
        .qspi_io_o            (qspi_io_o),
        .qspi_io_e            (qspi_io_e),
        .spi_sclk             (spi_sclk),
        .spi_mosi             (spi_mosi),
        .spi_miso             (spi_miso),
        .spi_ss               (spi_ss),
        .phc_pps_out          (phc_pps_out),
        .rtc_clk              (sys_fclk),  // feature (feature rtc_from_sys_fclk)
        .sys_scanenable       (1'b0),  // tied (excluded pad -> tied)
        .sys_testmode         (1'b0),  // tied (excluded pad -> tied)
        .dap_tdi              (1'b0),  // tied (excluded pad -> tied)
        .dap_ntrst            (1'b1),  // tied (excluded pad -> tied)
        .dap_tdo              (),  // open (excluded pad -> open)
        .dap_ntdoen           (),  // open (excluded pad -> open)
        .hostio4_p1_in        (1'b0),  // tied (excluded pad -> tied)
        .hostio4_p1_out       (),  // open (excluded pad -> open)
        .hostio4_p1_outen     (),  // open (excluded pad -> open)
        .sys_sysresetreq      (1'b0),  // tied
        .dap_npotrst          (nrst),  // feature (feature dap_npotrst_from_nrst)
        .dap_swj_enable       (1'b1),  // tied
        .network_core_pmuenable (1'b0),  // tied
        .chip_core_pmuenable  (1'b0),  // tied
        .network_core_nmi     (1'b0),  // tied
        .chip_core_nmi        (1'b0),  // tied
        .network_core_rxev    (1'b0),  // tied
        .chip_core_rxev       (1'b0),  // tied
        .eth_ss_0_htrans      (2'b00),  // tied
        .eth_ss_0_haddr       (32'd0),  // tied
        .eth_ss_0_hwrite      (1'b0),  // tied
        .eth_ss_0_hsize       (3'b000),  // tied
        .eth_ss_0_hburst      (3'b000),  // tied
        .eth_ss_0_hprot       (4'b0000),  // tied
        .eth_ss_0_hwdata      (32'd0),  // tied
        .eth_ss_0_hmastlock   (1'b0),  // tied
        .d2d_ahb_s_htrans     (2'b00),  // tied
        .d2d_ahb_s_haddr      (32'd0),  // tied
        .d2d_ahb_s_hwrite     (1'b0),  // tied
        .d2d_ahb_s_hsize      (3'b010),  // tied
        .d2d_ahb_s_hburst     (3'b000),  // tied
        .d2d_ahb_s_hprot      (4'b0000),  // tied
        .d2d_ahb_s_hwdata     (32'd0),  // tied
        .d2d_ahb_s_hmastlock  (1'b0),  // tied
        .d2d_irq              (16'd0),  // tied
        .d2d_phc_hw_capture   (1'b0),  // tied
        .d2d_phc_hw_set_time  (1'b0),  // tied
        .d2d_phc_hw_set_seconds (48'd0),  // tied
        .d2d_phc_hw_set_nanoseconds (30'd0),  // tied
        .d2d_phc_hw_adj_valid (1'b0),  // tied
        .d2d_phc_hw_adj_ns_incr_frac (32'd0),  // tied
        .sys_poresetn         (),  // open
        .sys_hclk             (sys_hclk),  // tap (status export -> sys_hclk_dbg)
        .sys_hresetn          (sys_hresetn),  // tap (status export -> sys_hresetn_o)
        .network_core_txev    (),  // open
        .network_core_lockup  (cpu_0_lockup_dbg),  // tap (debug tap cpu_0_lockup_dbg)
        .network_core_sysresetreq (network_core_sysresetreq),  // feature_tap (feature tap sysresetreq_selfloop)
        .network_core_sleeping (),  // open
        .network_core_sleepdeep (),  // open
        .chip_core_txev       (),  // open
        .chip_core_lockup     (cpu_1_lockup_dbg),  // tap (debug tap cpu_1_lockup_dbg)
        .chip_core_sysresetreq (chip_core_sysresetreq),  // feature_tap (feature tap sysresetreq_selfloop)
        .chip_core_sleeping   (),  // open
        .chip_core_sleepdeep  (),  // open
        .chip_core_wdog_reset (cpu1_wdog_reset),  // tap (debug tap cpu1_wdog_reset)
        .d2d_ahb_m_haddr      (),  // open
        .d2d_ahb_m_hwrite     (),  // open
        .d2d_ahb_m_hsize      (),  // open
        .d2d_ahb_m_hburst     (),  // open
        .d2d_ahb_m_hprot      (),  // open
        .d2d_ahb_m_hwdata     (),  // open
        .d2d_ahb_m_hmastlock  (),  // open
        .d2d_ahb_s_hrdata     (),  // open
        .d2d_ahb_s_hready     (),  // open
        .d2d_ahb_s_hresp      (),  // open
        .d2d_phc_seconds      (),  // open
        .d2d_phc_nanoseconds  (),  // open
        .d2d_phc_hw_cap_seconds (),  // open
        .d2d_phc_hw_cap_nanoseconds (),  // open
        .d2d_phc_hw_cap_sub_nanoseconds (),  // open
        .rtc_time_ptp_ns      (),  // open
        .rtc_time_ptp_sec     (),  // open
        .rtc_time_one_pps     (),  // open
        .ha1588_servo_locked  (ptp_lock_dbg),  // tap (debug tap ptp_lock_dbg)
        .eth_irq              (eth_irq),  // tap (debug tap eth_irq)
        .phc_pps_irq          (),  // open
        .phc_alarm_irq        (),  // open
        .eth_ss_0_hrdata      (),  // open
        .eth_ss_0_hready      (),  // open
        .eth_ss_0_hresp       (),  // open
        .d2d_ahb_m_htrans     (d2d_ahb_m_htrans),  // terminated (terminator wire)
        .d2d_ahb_m_hrdata     (d2d_ahb_m_hrdata),  // terminated (terminator wire)
        .d2d_ahb_m_hready     (d2d_ahb_m_hready),  // terminated (terminator wire)
        .d2d_ahb_m_hresp      (d2d_ahb_m_hresp)  // terminated (terminator wire)
    );

endmodule
