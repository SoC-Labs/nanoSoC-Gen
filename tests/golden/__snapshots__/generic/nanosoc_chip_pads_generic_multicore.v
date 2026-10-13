//-----------------------------------------------------------------------------
// nanosoc_chip_pads_generic_multicore — mixed-I/O pad ring for nanosoc_multicore_chip
//
// Copyright 2021-2026 SoC Labs (www.soclabs.org), University of Southampton
// SPDX-License-Identifier: Apache-2.0
//-----------------------------------------------------------------------------
// AUTO-GENERATED FILE - DO NOT EDIT DIRECTLY.
// Template:  nanosoc_chip_pads_mixed.v.j2
// Generated: 2026-10-13 08:51:20
//-----------------------------------------------------------------------------
// Per-signal pad ring (kinds: input / output / tristate_out / bidir; single +
// bus).  Wraps the bonded chip top nanosoc_multicore_chip, instantiating one pad cell
// per bonded signal (bus -> per-bit cells) plus the power/ground ring.
//
//   input        -> input_cell   (receiver on, driver off)
//   output       -> output_cell  (driver on,  receiver off)
//   bidir        -> bidir_cell   (in/out + active-low OEN; pull = z & o)
//   tristate_out -> bidir_io_cell (no-pull; OEN driven, receive leg parked)
//
// Active-high chip output-enables are inverted into a per-pad active-low `_z`
// net (the pad cell's OEN convention); active-low enables wire straight through.
//-----------------------------------------------------------------------------

module nanosoc_chip_pads_generic_multicore (
  input  wire          SYS_FCLK,  // input <- in=sys_fclk
  input  wire          RMII_REF_CLK,  // input <- in=rmii_ref_clk
  input  wire          RTC_CLK,  // input <- in=rtc_clk
  input  wire          SYS_SYSRESETN,  // input <- in=sys_sysresetn
  input  wire          SYS_SCANENABLE,  // input <- in=sys_scanenable
  input  wire          SYS_TESTMODE,  // input <- in=sys_testmode
  input  wire          DAP_SWCLKTCK,  // input <- in=dap_swclktck
  inout  wire          SWDIO,  // bidir <- in=dap_swditms, out=dap_swdo, oe=dap_swdoen
  input  wire          JTAG_TDI,  // input <- in=dap_tdi
  input  wire          JTAG_NTRST,  // input <- in=dap_ntrst
  inout  wire          JTAG_TDO,  // tristate_out <- out=dap_tdo, oe=dap_ntdoen
  input  wire          UART_RXD,  // input <- in=uart_rxd
  output wire          UART_TXD,  // output <- out=uart_txd
  input  wire          CPU1_UART_RXD,  // input <- in=chip_core_uart_rxd
  output wire          CPU1_UART_TXD,  // output <- out=chip_core_uart_txd
  output wire [1:0]    RMII_TXD,  // output <- out=rmii_txd
  output wire          RMII_TX_EN,  // output <- out=rmii_tx_en
  input  wire [1:0]    RMII_RXD,  // input <- in=rmii_rxd
  input  wire          RMII_CRS_DV,  // input <- in=rmii_crs_dv
  output wire          MDC,  // output <- out=mdc_pad_o
  inout  wire          MDIO,  // bidir <- in=md_pad_i, out=md_pad_o, oe=md_padoe_o
  output wire          QSPI_SCLK,  // output <- out=qspi_sclk
  output wire          QSPI_CSN,  // output <- out=qspi_csn
  inout  wire [3:0]    QSPI_IO,  // bidir <- in=qspi_io_i, out=qspi_io_o, oe=qspi_io_e
  output wire          SPI_SCLK,  // output <- out=spi_sclk
  output wire          SPI_MOSI,  // output <- out=spi_mosi
  input  wire          SPI_MISO,  // input <- in=spi_miso
  output wire [2:0]    SPI_SS,  // output <- out=spi_ss
  inout  wire [6:0]    HOSTIO4,  // bidir <- in=hostio4_p1_in, out=hostio4_p1_out, oe=hostio4_p1_outen
  output wire          PPS  // output <- out=phc_pps_out
  );

//------------------------------------
// internal nets bridging pad cells <-> nanosoc_multicore_chip
//  - chip-top in/out/oe nets (driven/read by the pad cells)
//  - active-low output-enable nets (_z) derived from the chip's OE polarity
//  - dangling receive-leg sinks (_nc) for output / tristate pads

  wire           sys_fclk;
  wire           rmii_ref_clk;
  wire           rtc_clk;
  wire           sys_sysresetn;
  wire           sys_scanenable;
  wire           sys_testmode;
  wire           dap_swclktck;
  wire           dap_swditms;
  wire           dap_swdo;
  wire           dap_swdoen;
  wire           dap_tdi;
  wire           dap_ntrst;
  wire           dap_tdo;
  wire           dap_ntdoen;
  wire           uart_rxd;
  wire           uart_txd;
  wire           chip_core_uart_rxd;
  wire           chip_core_uart_txd;
  wire  [1:0]    rmii_txd;
  wire           rmii_tx_en;
  wire  [1:0]    rmii_rxd;
  wire           rmii_crs_dv;
  wire           mdc_pad_o;
  wire           md_pad_i;
  wire           md_pad_o;
  wire           md_padoe_o;
  wire           qspi_sclk;
  wire           qspi_csn;
  wire  [3:0]    qspi_io_i;
  wire  [3:0]    qspi_io_o;
  wire  [3:0]    qspi_io_e;
  wire           spi_sclk;
  wire           spi_mosi;
  wire           spi_miso;
  wire  [2:0]    spi_ss;
  wire  [6:0]    hostio4_p1_in;
  wire  [6:0]    hostio4_p1_out;
  wire  [6:0]    hostio4_p1_outen;
  wire           phc_pps_out;

  wire           swdio_z = ~dap_swdoen;  // active-low OEN
  wire           jtag_tdo_z = dap_ntdoen;  // active-low OEN
  wire           mdio_z = ~md_padoe_o;  // active-low OEN
  wire  [3:0]    qspi_io_z = ~qspi_io_e;  // active-low OEN
  wire  [6:0]    hostio4_z = ~hostio4_p1_outen;  // active-low OEN
  wire           jtag_tdo_nc;  // unconnected receive leg
  wire           uart_txd_nc;  // unconnected receive leg
  wire           cpu1_uart_txd_nc;  // unconnected receive leg
  wire  [1:0]    rmii_txd_nc;  // unconnected receive leg
  wire           rmii_tx_en_nc;  // unconnected receive leg
  wire           mdc_nc;  // unconnected receive leg
  wire           qspi_sclk_nc;  // unconnected receive leg
  wire           qspi_csn_nc;  // unconnected receive leg
  wire           spi_sclk_nc;  // unconnected receive leg
  wire           spi_mosi_nc;  // unconnected receive leg
  wire  [2:0]    spi_ss_nc;  // unconnected receive leg
  wire           phc_pps_out_nc;  // unconnected receive leg

 wire tiehi = 1'b1;
 wire tielo = 1'b0;

 // --------------------------------------------------------------------------------
 // Bonded chip top
 // --------------------------------------------------------------------------------

  nanosoc_multicore_chip u_nanosoc_multicore_chip (
    .sys_fclk             (sys_fclk),
    .rmii_ref_clk         (rmii_ref_clk),
    .rtc_clk              (rtc_clk),
    .sys_sysresetn        (sys_sysresetn),
    .sys_scanenable       (sys_scanenable),
    .sys_testmode         (sys_testmode),
    .dap_swclktck         (dap_swclktck),
    .dap_swditms          (dap_swditms),
    .dap_swdo             (dap_swdo),
    .dap_swdoen           (dap_swdoen),
    .dap_tdi              (dap_tdi),
    .dap_ntrst            (dap_ntrst),
    .dap_tdo              (dap_tdo),
    .dap_ntdoen           (dap_ntdoen),
    .uart_rxd             (uart_rxd),
    .uart_txd             (uart_txd),
    .chip_core_uart_rxd   (chip_core_uart_rxd),
    .chip_core_uart_txd   (chip_core_uart_txd),
    .rmii_txd             (rmii_txd),
    .rmii_tx_en           (rmii_tx_en),
    .rmii_rxd             (rmii_rxd),
    .rmii_crs_dv          (rmii_crs_dv),
    .mdc_pad_o            (mdc_pad_o),
    .md_pad_i             (md_pad_i),
    .md_pad_o             (md_pad_o),
    .md_padoe_o           (md_padoe_o),
    .qspi_sclk            (qspi_sclk),
    .qspi_csn             (qspi_csn),
    .qspi_io_i            (qspi_io_i),
    .qspi_io_o            (qspi_io_o),
    .qspi_io_e            (qspi_io_e),
    .spi_sclk             (spi_sclk),
    .spi_mosi             (spi_mosi),
    .spi_miso             (spi_miso),
    .spi_ss               (spi_ss),
    .hostio4_p1_in        (hostio4_p1_in),
    .hostio4_p1_out       (hostio4_p1_out),
    .hostio4_p1_outen     (hostio4_p1_outen),
    .phc_pps_out          (phc_pps_out)
  );

 // --------------------------------------------------------------------------------
 // IO pads (pad-technology library mapping: generic)
 // --------------------------------------------------------------------------------

// Pad IO power supplies

PAD_VDDIO uPAD_VDDIO_0(
   .PAD(VDDIO)
   );
PAD_VDDIO uPAD_VDDIO_1(
   .PAD(VDDIO)
   );
PAD_VDDIO uPAD_VDDIO_2(
   .PAD(VDDIO)
   );
PAD_VSSIO uPAD_VSSIO_0(
   .PAD(VSS)
   );
PAD_VSSIO uPAD_VSSIO_1(
   .PAD(VSS)
   );
PAD_VDDSOC uPAD_VDD_0(
   .PAD(VDD)
   );
PAD_VDDSOC uPAD_VDD_1(
   .PAD(VDD)
   );
PAD_VDDSOC uPAD_VDD_2(
   .PAD(VDD)
   );
PAD_VDDSOC uPAD_VDD_3(
   .PAD(VDD)
   );
PAD_VSS uPAD_VSS_0(
   .PAD(VSS)
   );
PAD_VSS uPAD_VSS_1(
   .PAD(VSS)
   );
PAD_VSS uPAD_VSS_2(
   .PAD(VSS)
   );
PAD_VSS uPAD_VSS_3(
   .PAD(VSS)
   );
PAD_VDDSOC uPAD_VDDACC_0(
   .PAD(VDDACC)
   );
PAD_VDDSOC uPAD_VDDACC_1(
   .PAD(VDDACC)
   );
PAD_VDDSOC uPAD_VDDACC_2(
   .PAD(VDDACC)
   );

// Signal pads

PAD_INOUT8MA_NOE uPAD_SYS_FCLK (
    .PAD(SYS_FCLK),
    .O(tielo),
    .I(sys_fclk),
    .NOE(tiehi)
   );

PAD_INOUT8MA_NOE uPAD_RMII_REF_CLK (
    .PAD(RMII_REF_CLK),
    .O(tielo),
    .I(rmii_ref_clk),
    .NOE(tiehi)
   );

PAD_INOUT8MA_NOE uPAD_RTC_CLK (
    .PAD(RTC_CLK),
    .O(tielo),
    .I(rtc_clk),
    .NOE(tiehi)
   );

PAD_INOUT8MA_NOE uPAD_SYS_SYSRESETN (
    .PAD(SYS_SYSRESETN),
    .O(tielo),
    .I(sys_sysresetn),
    .NOE(tiehi)
   );

PAD_INOUT8MA_NOE uPAD_SYS_SCANENABLE (
    .PAD(SYS_SCANENABLE),
    .O(tielo),
    .I(sys_scanenable),
    .NOE(tiehi)
   );

PAD_INOUT8MA_NOE uPAD_SYS_TESTMODE (
    .PAD(SYS_TESTMODE),
    .O(tielo),
    .I(sys_testmode),
    .NOE(tiehi)
   );

PAD_INOUT8MA_NOE uPAD_DAP_SWCLKTCK (
    .PAD(DAP_SWCLKTCK),
    .O(tielo),
    .I(dap_swclktck),
    .NOE(tiehi)
   );

PAD_INOUT8MA_NOE uPAD_SWDIO (
    .PAD(SWDIO),
    .O(dap_swdo),
    .I(dap_swditms),
    .NOE(swdio_z)
   );

PAD_INOUT8MA_NOE uPAD_JTAG_TDI (
    .PAD(JTAG_TDI),
    .O(tielo),
    .I(dap_tdi),
    .NOE(tiehi)
   );

PAD_INOUT8MA_NOE uPAD_JTAG_NTRST (
    .PAD(JTAG_NTRST),
    .O(tielo),
    .I(dap_ntrst),
    .NOE(tiehi)
   );

PAD_INOUT8MA_NOE uPAD_JTAG_TDO (
    .PAD(JTAG_TDO),
    .O(dap_tdo),
    .I(jtag_tdo_nc),
    .NOE(jtag_tdo_z)
   );

PAD_INOUT8MA_NOE uPAD_UART_RXD (
    .PAD(UART_RXD),
    .O(tielo),
    .I(uart_rxd),
    .NOE(tiehi)
   );

PAD_INOUT8MA_NOE uPAD_UART_TXD (
    .PAD(UART_TXD),
    .O(uart_txd),
    .I(uart_txd_nc),
    .NOE(tielo)
   );

PAD_INOUT8MA_NOE uPAD_CPU1_UART_RXD (
    .PAD(CPU1_UART_RXD),
    .O(tielo),
    .I(chip_core_uart_rxd),
    .NOE(tiehi)
   );

PAD_INOUT8MA_NOE uPAD_CPU1_UART_TXD (
    .PAD(CPU1_UART_TXD),
    .O(chip_core_uart_txd),
    .I(cpu1_uart_txd_nc),
    .NOE(tielo)
   );

PAD_INOUT8MA_NOE uPAD_RMII_TXD_0 (
    .PAD(RMII_TXD[0]),
    .O(rmii_txd[0]),
    .I(rmii_txd_nc[0]),
    .NOE(tielo)
   );

PAD_INOUT8MA_NOE uPAD_RMII_TXD_1 (
    .PAD(RMII_TXD[1]),
    .O(rmii_txd[1]),
    .I(rmii_txd_nc[1]),
    .NOE(tielo)
   );

PAD_INOUT8MA_NOE uPAD_RMII_TX_EN (
    .PAD(RMII_TX_EN),
    .O(rmii_tx_en),
    .I(rmii_tx_en_nc),
    .NOE(tielo)
   );

PAD_INOUT8MA_NOE uPAD_RMII_RXD_0 (
    .PAD(RMII_RXD[0]),
    .O(tielo),
    .I(rmii_rxd[0]),
    .NOE(tiehi)
   );

PAD_INOUT8MA_NOE uPAD_RMII_RXD_1 (
    .PAD(RMII_RXD[1]),
    .O(tielo),
    .I(rmii_rxd[1]),
    .NOE(tiehi)
   );

PAD_INOUT8MA_NOE uPAD_RMII_CRS_DV (
    .PAD(RMII_CRS_DV),
    .O(tielo),
    .I(rmii_crs_dv),
    .NOE(tiehi)
   );

PAD_INOUT8MA_NOE uPAD_MDC (
    .PAD(MDC),
    .O(mdc_pad_o),
    .I(mdc_nc),
    .NOE(tielo)
   );

PAD_INOUT8MA_NOE uPAD_MDIO (
    .PAD(MDIO),
    .O(md_pad_o),
    .I(md_pad_i),
    .NOE(mdio_z)
   );

PAD_INOUT8MA_NOE uPAD_QSPI_SCLK (
    .PAD(QSPI_SCLK),
    .O(qspi_sclk),
    .I(qspi_sclk_nc),
    .NOE(tielo)
   );

PAD_INOUT8MA_NOE uPAD_QSPI_CSN (
    .PAD(QSPI_CSN),
    .O(qspi_csn),
    .I(qspi_csn_nc),
    .NOE(tielo)
   );

PAD_INOUT8MA_NOE uPAD_QSPI_IO_0 (
    .PAD(QSPI_IO[0]),
    .O(qspi_io_o[0]),
    .I(qspi_io_i[0]),
    .NOE(qspi_io_z[0])
   );

PAD_INOUT8MA_NOE uPAD_QSPI_IO_1 (
    .PAD(QSPI_IO[1]),
    .O(qspi_io_o[1]),
    .I(qspi_io_i[1]),
    .NOE(qspi_io_z[1])
   );

PAD_INOUT8MA_NOE uPAD_QSPI_IO_2 (
    .PAD(QSPI_IO[2]),
    .O(qspi_io_o[2]),
    .I(qspi_io_i[2]),
    .NOE(qspi_io_z[2])
   );

PAD_INOUT8MA_NOE uPAD_QSPI_IO_3 (
    .PAD(QSPI_IO[3]),
    .O(qspi_io_o[3]),
    .I(qspi_io_i[3]),
    .NOE(qspi_io_z[3])
   );

PAD_INOUT8MA_NOE uPAD_SPI_SCLK (
    .PAD(SPI_SCLK),
    .O(spi_sclk),
    .I(spi_sclk_nc),
    .NOE(tielo)
   );

PAD_INOUT8MA_NOE uPAD_SPI_MOSI (
    .PAD(SPI_MOSI),
    .O(spi_mosi),
    .I(spi_mosi_nc),
    .NOE(tielo)
   );

PAD_INOUT8MA_NOE uPAD_SPI_MISO (
    .PAD(SPI_MISO),
    .O(tielo),
    .I(spi_miso),
    .NOE(tiehi)
   );

PAD_INOUT8MA_NOE uPAD_SPI_SS_0 (
    .PAD(SPI_SS[0]),
    .O(spi_ss[0]),
    .I(spi_ss_nc[0]),
    .NOE(tielo)
   );

PAD_INOUT8MA_NOE uPAD_SPI_SS_1 (
    .PAD(SPI_SS[1]),
    .O(spi_ss[1]),
    .I(spi_ss_nc[1]),
    .NOE(tielo)
   );

PAD_INOUT8MA_NOE uPAD_SPI_SS_2 (
    .PAD(SPI_SS[2]),
    .O(spi_ss[2]),
    .I(spi_ss_nc[2]),
    .NOE(tielo)
   );

PAD_INOUT8MA_NOE uPAD_HOSTIO4_0 (
    .PAD(HOSTIO4[0]),
    .O(hostio4_p1_out[0]),
    .I(hostio4_p1_in[0]),
    .NOE(hostio4_z[0])
   );

PAD_INOUT8MA_NOE uPAD_HOSTIO4_1 (
    .PAD(HOSTIO4[1]),
    .O(hostio4_p1_out[1]),
    .I(hostio4_p1_in[1]),
    .NOE(hostio4_z[1])
   );

PAD_INOUT8MA_NOE uPAD_HOSTIO4_2 (
    .PAD(HOSTIO4[2]),
    .O(hostio4_p1_out[2]),
    .I(hostio4_p1_in[2]),
    .NOE(hostio4_z[2])
   );

PAD_INOUT8MA_NOE uPAD_HOSTIO4_3 (
    .PAD(HOSTIO4[3]),
    .O(hostio4_p1_out[3]),
    .I(hostio4_p1_in[3]),
    .NOE(hostio4_z[3])
   );

PAD_INOUT8MA_NOE uPAD_HOSTIO4_4 (
    .PAD(HOSTIO4[4]),
    .O(hostio4_p1_out[4]),
    .I(hostio4_p1_in[4]),
    .NOE(hostio4_z[4])
   );

PAD_INOUT8MA_NOE uPAD_HOSTIO4_5 (
    .PAD(HOSTIO4[5]),
    .O(hostio4_p1_out[5]),
    .I(hostio4_p1_in[5]),
    .NOE(hostio4_z[5])
   );

PAD_INOUT8MA_NOE uPAD_HOSTIO4_6 (
    .PAD(HOSTIO4[6]),
    .O(hostio4_p1_out[6]),
    .I(hostio4_p1_in[6]),
    .NOE(hostio4_z[6])
   );

PAD_INOUT8MA_NOE uPAD_PHC_PPS_OUT (
    .PAD(PPS),
    .O(phc_pps_out),
    .I(phc_pps_out_nc),
    .NOE(tielo)
   );

endmodule
