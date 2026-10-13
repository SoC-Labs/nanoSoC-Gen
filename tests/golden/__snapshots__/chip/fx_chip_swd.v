//-----------------------------------------------------------------------------
// fx_chip Chip-level File
//
// Copyright 2021-2026 SoC Labs (www.soclabs.org), University of Southampton
// SPDX-License-Identifier: Apache-2.0
//-----------------------------------------------------------------------------
// AUTO-GENERATED FILE - DO NOT EDIT DIRECTLY.
// Template:  soc_chip.v.j2
// Generated: 2026-09-22 12:20:31
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

  // Serial-Wire Debug
  wire                     CPU_0_SWDI;         // SWD data input
  wire                     CPU_0_SWCLK;        // SWD clock
  wire                     CPU_0_SWDO;         // SWD data output
  wire                     CPU_0_SWDOEN;       // SWD data enable

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
  // SWD Wiring
  //--------------------------

  assign CPU_0_SWCLK = swdclk_i;
  assign CPU_0_SWDI  = swdio_i;
  assign swdio_o     = CPU_0_SWDO;
  assign swdio_e     = CPU_0_SWDOEN;
  assign swdio_z     = !CPU_0_SWDOEN;

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
  // DMA Controller Type Selection
  //--------------------------
  // These MUST track DMAC_0_TYPE / DMAC_1_TYPE in
  // build_soc/rtl/fx_soc_config_pkg.sv (generated from the YAML).
  // They are resolved here as Verilog-2001 localparams rather than as
  // package-scope references into fx_soc_config_pkg because:
  //   1. fx_soc_config_pkg.sv is only in the TESTBENCH filelist
  //      (fx_tb.flist), not in the design filelist (fx_ip.flist),
  //      so the package is simply not visible to any non-simulation flow; and
  //   2. Vivado's IP packager parses this .v top as Verilog-2001 and rejects
  //      the SystemVerilog scope-resolution operator outright
  //      (HDL 9-1206 / IP_Flow 19-259).
  // Defaults are 0 (no DMA), matching the YAML for this SoC; the defines
  // below keep the same override mechanism as chip/chip/verilog/fx_chip.v.
`ifdef DMAC_DMA350
  localparam DMAC_0_TYPE_CFG = 2;  // DMA350 dual-port
`elsif DMAC_0_PL230
  localparam DMAC_0_TYPE_CFG = 1;  // PL230 single-port
`else
  localparam DMAC_0_TYPE_CFG = 0;  // None
`endif

`ifdef DMAC_1_PL230
  localparam DMAC_1_TYPE_CFG = 1;  // PL230 single-port
`else
  localparam DMAC_1_TYPE_CFG = 0;  // None
`endif

  //--------------------------
  // System Instantiation
  //--------------------------

  fx_system #(
      .DMAC_0_TYPE(DMAC_0_TYPE_CFG),
      .DMAC_1_TYPE(DMAC_1_TYPE_CFG)
  ) u_system (
      // Free-running and Crystal Clock Output
      .SYS_CLK(SYS_CLK),
      .SYS_XTALCLK_OUT(SYS_XTALCLK_OUT),
      .SYS_SYSRESETn(nrst_i),

      // Scan Wiring
      .SYS_SCANENABLE(SYS_SCANENABLE),
      .SYS_TESTMODE(SYS_TESTMODE),
      .SYS_SCANINHCLK(SYS_SCANINHCLK),
      .SYS_SCANOUTHCLK(SYS_SCANOUTHCLK),

      // Serial-Wire Debug
      .CPU_0_SWDI(CPU_0_SWDI),
      .CPU_0_SWCLK(CPU_0_SWCLK),
      .CPU_0_SWDO(CPU_0_SWDO),
      .CPU_0_SWDOEN(CPU_0_SWDOEN),

      // GPIO
      .P0_IN(P0_IN),
      .P0_OUT(P0_OUT),
      .P0_OUTEN(P0_OUTEN),
      .P1_IN(P1_IN),
      .P1_OUT(P1_OUT),
      .P1_OUTEN(P1_OUTEN)
  );

endmodule
