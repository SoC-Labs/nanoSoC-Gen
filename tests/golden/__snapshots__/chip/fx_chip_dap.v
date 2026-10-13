//-----------------------------------------------------------------------------
// fx_chip Chip-level File
//
// Copyright 2021-2026 SoC Labs (www.soclabs.org), University of Southampton
// SPDX-License-Identifier: Apache-2.0
//-----------------------------------------------------------------------------
// AUTO-GENERATED FILE - DO NOT EDIT DIRECTLY.
// Template:  soc_chip.v.j2
// Generated: 2026-09-22 12:16:47
//-----------------------------------------------------------------------------
module fx_chip #(
  parameter integer FT1248_WIDTH = 1, // Only 1-bit serial supported for min pincount
  parameter integer GPIO_TIO	 = 4 // reconfigure lowest four GPIO ports for test
)(
`ifdef POWER_PINS
  inout  wire          VDD,
  inout  wire          VSS,
  inout  wire          VDDACC,
`endif
  input  wire                diag_mode,
  input  wire                diag_ctrl,
  input  wire                scan_mode,
  input  wire                scan_enable,
  input  wire [GPIO_TIO-1:0] scan_in,     // soc test scan chain inputs
  output wire [GPIO_TIO-1:0] scan_out,    // soc test scan chain outputs
  input  wire                bist_mode,
  input  wire                bist_enable,
  input  wire [GPIO_TIO-1:0] bist_in,     // soc bist control inputs
  output wire [GPIO_TIO-1:0] bist_out,    // soc test status outputs
  input  wire                alt_mode,    // ALT MODE = UART
  input  wire                uart_rxd_i,  // UART RXD
  output wire                uart_txd_o,  // UART TXD
  input  wire                swd_mode,    // SWD mode
  input  wire          clk_i,
  input  wire          test_i,
  input  wire          nrst_i,
  input  wire  [15:0]  p0_i, // level-shifted input from pad
  output wire  [15:0]  p0_o, // output port drive
  output wire  [15:0]  p0_e, // active high output drive enable (pad tech dependent)
  output wire  [15:0]  p0_z, // active low output drive enable (pad tech dependent)
  input  wire  [15:0]  p1_i, // level-shifted input from pad
  output wire  [15:0]  p1_o, // output port drive
  output wire  [15:0]  p1_e, // active high output drive enable (pad tech dependent)
  output wire  [15:0]  p1_z, // active low output drive enable (pad tech dependent)
  input  wire          dap_swclktck,  // CoreSight SWJ-DAP boundary -- plain chip port for the FPGA debug flow
  input  wire          dap_swditms,  // CoreSight SWJ-DAP boundary -- plain chip port for the FPGA debug flow
  output wire          dap_swdo,  // CoreSight SWJ-DAP boundary -- plain chip port for the FPGA debug flow
  output wire          dap_swdoen,  // CoreSight SWJ-DAP boundary -- plain chip port for the FPGA debug flow
  input  wire          swdio_i, // alternate test clock control
  output wire          swdio_o,
  output wire          swdio_e,
  output wire          swdio_z,
  input  wire          swdclk_i // alternate test scan enable
);

  //--------------------------
  // Power Supply Tie-offs (non-ASIC)
  //--------------------------
`ifndef POWER_PINS
  supply1 VDD;
  supply1 VDDACC;
  supply0 VSS;
`endif

  //--------------------------
  // System Wiring
  //--------------------------
  // Free-running and Crystal Clock Output
  wire                     SYS_CLK;              // System Input Clock
  wire                     PLL_CLK;              // Phase-Locked Loop Clock
  wire                     SYS_XTALCLK_OUT;      // Crystal Clock Output

  // Scan Wiring
  wire                     SYS_SCANENABLE;      // Scan Mode Enable
  wire                     SYS_TESTMODE;        // Test Mode Enable (Override Synchronisers)
  wire                     SYS_SCANINHCLK;      // HCLK scan wire - TIE OFF
  wire                     SYS_SCANOUTHCLK;     // Scan Chain Output - UNUSED

  // GPIO
  wire              [15:0] P0_IN;            // GPIO P0 inputs
  wire              [15:0] P0_OUT;           // GPIO P0 outputs
  wire              [15:0] P0_OUTEN;         // GPIO P0 output enables

  wire              [15:0] P1_IN;            // GPIO P1 inputs
  wire              [15:0] P1_OUT;           // GPIO P1 outputs
  wire              [15:0] P1_OUTEN;         // GPIO P1 output enables

  //--------------------------
  // FPGA-Specific Wiring - Should be own Module
  //--------------------------

  // Technology-specific PLL/Frequecy synthesizer would generate
  //   CLK, FCLK (Free running system clock)
  // from
  //    clk_i

  assign PLL_CLK    = clk_i; // Default to no PLL

  assign SYS_SCANENABLE   = scan_enable;
  assign SYS_TESTMODE     = scan_mode;
  assign SYS_SCANINHCLK   = 1'b1;
  assign bist_out         = bist_in;

  //--------------------------
  // Clock Wiring
  //--------------------------

  assign SYS_CLK    = (SYS_TESTMODE) ? clk_i : PLL_CLK;

  //--------------------------
  // Legacy SWDIO/SWDCK pads (retained, inert)
  //--------------------------
  // Debug now leaves the die through the dap_* group (passed straight to the
  // system for the FPGA flow). The legacy SWDIO/SWDCK bond-out pads are kept so
  // the pad ring is unchanged, but they are no longer on the debug path: hold
  // the SWDIO pad output disabled (Hi-Z); swd_mode/swdio_i/swdclk_i idle.

  assign swdio_o     = 1'b0;
  assign swdio_e     = 1'b0;
  assign swdio_z     = 1'b1;

  //--------------------------
  // PIO pad control
  //--------------------------

  assign P0_IN = p0_i;
  assign p0_o  = P0_OUT;
  assign p0_e  = P0_OUTEN;
  assign p0_z  = ~P0_OUTEN;

  assign P1_IN = p1_i;
  assign p1_o  = P1_OUT;
  assign p1_e  = P1_OUTEN;
  assign p1_z  = ~P1_OUTEN;

  //--------------------------
  // System Instantiation
  // DMA type and other design parameters are sourced from the config package.
  //--------------------------

  fx_system u_system (
      // Free-running and Crystal Clock Output
      .SYS_CLK(SYS_CLK),
      .SYS_XTALCLK_OUT(SYS_XTALCLK_OUT),
      .SYS_SYSRESETn(nrst_i),

      // Scan Wiring
      .SYS_SCANENABLE(SYS_SCANENABLE),
      .SYS_TESTMODE(SYS_TESTMODE),
      .SYS_SCANINHCLK(SYS_SCANINHCLK),
      .SYS_SCANOUTHCLK(SYS_SCANOUTHCLK),

      // CoreSight SWJ-DAP boundary (passed straight through)
      .dap_swclktck(dap_swclktck),
      .dap_swditms(dap_swditms),
      .dap_swdo(dap_swdo),
      .dap_swdoen(dap_swdoen),
      // GPIO
      .P0_IN(P0_IN),
      .P0_OUT(P0_OUT),
      .P0_OUTEN(P0_OUTEN),
      .P1_IN(P1_IN),
      .P1_OUT(P1_OUT),
      .P1_OUTEN(P1_OUTEN)
  );

endmodule
