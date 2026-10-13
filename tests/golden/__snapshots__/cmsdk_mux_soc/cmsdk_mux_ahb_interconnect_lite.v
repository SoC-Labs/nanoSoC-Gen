`timescale 1ns/1ps
//-----------------------------------------------------------------------------
// cmsdk_mux_ahb_interconnect_lite -- AHB-Lite interconnect core, impl: cmsdk_mux
//
// Written by SoC Labs. Contains no Arm source. It instantiates three Arm
// Cortex-M0 Quickstart (Corstone-101) components, referenced by path through
// $(ARM_CORSTONE_101_DIR) in the interconnect filelist and never copied:
// cmsdk_ahb_master_mux, cmsdk_ahb_slave_mux, cmsdk_ahb_default_slave.
//
// Drop-in for the BuildBusMatrix-generated cmsdk_mux_ahb_interconnect_lite: identical
// module name and port list.
//
// Copyright (C) 2026, SoC Labs (www.soclabs.org)
//-----------------------------------------------------------------------------
// AUTO-GENERATED FILE - DO NOT EDIT DIRECTLY.
// Source:    soc_model (cmsdk_mux_ahb_interconnect)
// Template:  ahb_interconnect_cmsdk_mux_lite.v.j2
// Generated: 2026-09-26 23:46:14
//-----------------------------------------------------------------------------
// Topology
//   Shared bus: CPU_M -> S0, EXT_S -> S1
//     of u_master_mux (S0/S1 round-robin, S2 high priority)
//     -> decoder -> u_slave_mux -> ROM_0, RAM_0, DAT_0, DEFAULT
//   Private path: CPU_M -> SYSTEM (passthrough; never holds the shared bus)
//     CPU_M's HMASTLOCK reaches the shared master mux except while CPU_M drives
//     a NONSEQ/SEQ to the private path: a locked transfer there must not lock
//     the shared bus, or it recreates the deadlock the private path breaks.
//     An IDLE/BUSY inside a locked sequence keeps the lock whatever its
//     (don't-care) HADDR decodes to, so the sequence stays atomic.
//   Each target sees the bus as a matrix port would: HSEL is a pure address
//   decode and HREADYMUX is its own view (low only in its own waited data
//   phase). HTRANS shows the bus transfer when it is addressed to the target
//   and either the bus is ready or the target is itself in the data phase
//   (then its HREADYMUX IS the bus HREADY, so a waited NONSEQ/SEQ/BUSY stays
//   visible, as AHB requires). While ANOTHER target stalls the bus the target
//   is shown IDLE: its HREADYMUX is high then, and it must not take a
//   transfer the bus has not accepted.
// Decoder: HADDR[31:27], from the effective address maps for
// remap[0]=0; remap[0]=1.
//-----------------------------------------------------------------------------

module cmsdk_mux_ahb_interconnect_lite (
    // Common AHB signals
    input  wire        HCLK,
    input  wire        HRESETn,

    // System address remap control
    input  wire  [3:0] REMAP,

    // Initiator port CPU_M
    input  wire [31:0] HADDR_CPU_M,
    input  wire  [1:0] HTRANS_CPU_M,
    input  wire        HWRITE_CPU_M,
    input  wire  [2:0] HSIZE_CPU_M,
    input  wire  [2:0] HBURST_CPU_M,
    input  wire  [3:0] HPROT_CPU_M,
    input  wire [31:0] HWDATA_CPU_M,
    input  wire        HMASTLOCK_CPU_M,
    output wire [31:0] HRDATA_CPU_M,
    output wire        HREADY_CPU_M,
    output wire        HRESP_CPU_M,

    // Initiator port EXT_S
    input  wire [31:0] HADDR_EXT_S,
    input  wire  [1:0] HTRANS_EXT_S,
    input  wire        HWRITE_EXT_S,
    input  wire  [2:0] HSIZE_EXT_S,
    input  wire  [2:0] HBURST_EXT_S,
    input  wire  [3:0] HPROT_EXT_S,
    input  wire [31:0] HWDATA_EXT_S,
    input  wire        HMASTLOCK_EXT_S,
    output wire [31:0] HRDATA_EXT_S,
    output wire        HREADY_EXT_S,
    output wire        HRESP_EXT_S,

    // Target port ROM_0
    input  wire [31:0] HRDATA_ROM_0,
    input  wire        HREADYOUT_ROM_0,
    input  wire        HRESP_ROM_0,
    output wire        HSEL_ROM_0,
    output wire [31:0] HADDR_ROM_0,
    output wire  [1:0] HTRANS_ROM_0,
    output wire        HWRITE_ROM_0,
    output wire  [2:0] HSIZE_ROM_0,
    output wire  [2:0] HBURST_ROM_0,
    output wire  [3:0] HPROT_ROM_0,
    output wire [31:0] HWDATA_ROM_0,
    output wire        HMASTLOCK_ROM_0,
    output wire        HREADYMUX_ROM_0,

    // Target port RAM_0
    input  wire [31:0] HRDATA_RAM_0,
    input  wire        HREADYOUT_RAM_0,
    input  wire        HRESP_RAM_0,
    output wire        HSEL_RAM_0,
    output wire [31:0] HADDR_RAM_0,
    output wire  [1:0] HTRANS_RAM_0,
    output wire        HWRITE_RAM_0,
    output wire  [2:0] HSIZE_RAM_0,
    output wire  [2:0] HBURST_RAM_0,
    output wire  [3:0] HPROT_RAM_0,
    output wire [31:0] HWDATA_RAM_0,
    output wire        HMASTLOCK_RAM_0,
    output wire        HREADYMUX_RAM_0,

    // Target port DAT_0
    input  wire [31:0] HRDATA_DAT_0,
    input  wire        HREADYOUT_DAT_0,
    input  wire        HRESP_DAT_0,
    output wire        HSEL_DAT_0,
    output wire [31:0] HADDR_DAT_0,
    output wire  [1:0] HTRANS_DAT_0,
    output wire        HWRITE_DAT_0,
    output wire  [2:0] HSIZE_DAT_0,
    output wire  [2:0] HBURST_DAT_0,
    output wire  [3:0] HPROT_DAT_0,
    output wire [31:0] HWDATA_DAT_0,
    output wire        HMASTLOCK_DAT_0,
    output wire        HREADYMUX_DAT_0,

    // Target port SYSTEM
    input  wire [31:0] HRDATA_SYSTEM,
    input  wire        HREADYOUT_SYSTEM,
    input  wire        HRESP_SYSTEM,
    output wire        HSEL_SYSTEM,
    output wire [31:0] HADDR_SYSTEM,
    output wire  [1:0] HTRANS_SYSTEM,
    output wire        HWRITE_SYSTEM,
    output wire  [2:0] HSIZE_SYSTEM,
    output wire  [2:0] HBURST_SYSTEM,
    output wire  [3:0] HPROT_SYSTEM,
    output wire [31:0] HWDATA_SYSTEM,
    output wire        HMASTLOCK_SYSTEM,
    output wire        HREADYMUX_SYSTEM,

    // Scan ports: this core has no flops on a scan chain (SCANOUTHCLK = SCANINHCLK)
    input  wire        SCANENABLE,
    input  wire        SCANINHCLK,
    output wire        SCANOUTHCLK
);

  localparam [1:0] TRN_IDLE = 2'b00;

  // ---------------------------------------------------------------------------
  // Initiator CPU_M: shared-bus port u_master_mux.S0
  // ---------------------------------------------------------------------------
  wire                  s_CPU_M_hreadyout;   // from the master mux
  wire                  s_CPU_M_hresp;
  wire [31:0] s_CPU_M_hrdata;
  // Front end: private passthrough target(s) split off before the shared bus
  wire [4:0] fe_CPU_M_dec_a = HADDR_CPU_M[31:27];
  wire fe_CPU_M_sel_SYSTEM =
      (fe_CPU_M_dec_a >= 5'h4);
  wire fe_CPU_M_sel_shared = ~(fe_CPU_M_sel_SYSTEM);
  wire fe_CPU_M_hready;                  // this initiator's HREADY

  cmsdk_ahb_slave_mux #(
    .PORT0_ENABLE(1),
    .PORT1_ENABLE(1),
    .PORT2_ENABLE(0),
    .PORT3_ENABLE(0),
    .PORT4_ENABLE(0),
    .PORT5_ENABLE(0),
    .PORT6_ENABLE(0),
    .PORT7_ENABLE(0),
    .PORT8_ENABLE(0),
    .PORT9_ENABLE(0),
    .DW(32)
  ) u_fe_cpu_m_slave_mux (
    .HCLK(HCLK), .HRESETn(HRESETn), .HREADY(fe_CPU_M_hready),
    .HSEL0(fe_CPU_M_sel_shared), .HREADYOUT0(s_CPU_M_hreadyout), .HRESP0(s_CPU_M_hresp), .HRDATA0(s_CPU_M_hrdata),
    .HSEL1(fe_CPU_M_sel_SYSTEM), .HREADYOUT1(HREADYOUT_SYSTEM), .HRESP1(HRESP_SYSTEM), .HRDATA1(HRDATA_SYSTEM),
    .HSEL2(1'b0), .HREADYOUT2(1'b1), .HRESP2(1'b0), .HRDATA2(32'h0),
    .HSEL3(1'b0), .HREADYOUT3(1'b1), .HRESP3(1'b0), .HRDATA3(32'h0),
    .HSEL4(1'b0), .HREADYOUT4(1'b1), .HRESP4(1'b0), .HRDATA4(32'h0),
    .HSEL5(1'b0), .HREADYOUT5(1'b1), .HRESP5(1'b0), .HRDATA5(32'h0),
    .HSEL6(1'b0), .HREADYOUT6(1'b1), .HRESP6(1'b0), .HRDATA6(32'h0),
    .HSEL7(1'b0), .HREADYOUT7(1'b1), .HRESP7(1'b0), .HRDATA7(32'h0),
    .HSEL8(1'b0), .HREADYOUT8(1'b1), .HRESP8(1'b0), .HRDATA8(32'h0),
    .HSEL9(1'b0), .HREADYOUT9(1'b1), .HRESP9(1'b0), .HRDATA9(32'h0),
    .HREADYOUT(fe_CPU_M_hready), .HRESP(HRESP_CPU_M), .HRDATA(HRDATA_CPU_M)
  );
  assign HREADY_CPU_M = fe_CPU_M_hready;

  // Private target SYSTEM: driven straight from CPU_M
  reg    fe_CPU_M_dp_SYSTEM;               // SYSTEM is in its data phase
  always @(posedge HCLK or negedge HRESETn)
    if (!HRESETn)                fe_CPU_M_dp_SYSTEM <= 1'b0;
    else if (fe_CPU_M_hready) fe_CPU_M_dp_SYSTEM <= HSEL_SYSTEM & HTRANS_SYSTEM[1];
  assign HSEL_SYSTEM      = fe_CPU_M_sel_SYSTEM;
  assign HADDR_SYSTEM     = HADDR_CPU_M;
  // Shown while CPU_M's HREADY is high or SYSTEM is in its own (waited) data
  // phase, when HREADYMUX_SYSTEM equals CPU_M's HREADY. Hidden while a shared-bus
  // data phase stalls CPU_M: SYSTEM would take the transfer early.
  assign HTRANS_SYSTEM    = (fe_CPU_M_sel_SYSTEM & (fe_CPU_M_hready | fe_CPU_M_dp_SYSTEM)) ? HTRANS_CPU_M : TRN_IDLE;
  assign HWRITE_SYSTEM    = HWRITE_CPU_M;
  assign HSIZE_SYSTEM     = HSIZE_CPU_M;
  assign HBURST_SYSTEM    = HBURST_CPU_M;
  assign HPROT_SYSTEM     = HPROT_CPU_M;
  assign HWDATA_SYSTEM    = HWDATA_CPU_M;
  assign HMASTLOCK_SYSTEM = HMASTLOCK_CPU_M;
  assign HREADYMUX_SYSTEM = fe_CPU_M_dp_SYSTEM ? HREADYOUT_SYSTEM : 1'b1;

  // ---------------------------------------------------------------------------
  // Initiator EXT_S: shared-bus port u_master_mux.S1
  // ---------------------------------------------------------------------------
  wire                  s_EXT_S_hreadyout;   // from the master mux
  wire                  s_EXT_S_hresp;
  wire [31:0] s_EXT_S_hrdata;
  // AHB-Lite initiator: always selected, its HREADY is its own HREADYOUT
  assign HREADY_EXT_S = s_EXT_S_hreadyout;
  assign HRESP_EXT_S  = s_EXT_S_hresp;
  assign HRDATA_EXT_S = s_EXT_S_hrdata;

  // ---------------------------------------------------------------------------
  // Shared bus
  // ---------------------------------------------------------------------------
  wire        b_hsel;
  wire [31:0] b_haddr;
  wire  [1:0] b_htrans;
  wire        b_hwrite;
  wire  [2:0] b_hsize;
  wire  [2:0] b_hburst;
  wire  [3:0] b_hprot;
  wire        b_hmastlock;
  wire [31:0] b_hwdata;
  wire        b_hready;          // bus HREADY, from the master mux
  wire        b_hreadyout;       // selected target's HREADYOUT, from the slave mux
  wire        b_hresp;
  wire [31:0] b_hrdata;
  wire  [1:0] b_hmaster;         // address-phase master of u_master_mux
  wire                  nc_top_s2_hreadyout;   // disabled port, unused
  wire                  nc_top_s2_hresp;
  wire [31:0] nc_top_s2_hrdata;

  cmsdk_ahb_master_mux #(
    .PORT0_ENABLE(1), .PORT1_ENABLE(1), .PORT2_ENABLE(0), .DW(32)
  ) u_master_mux (
    .HCLK(HCLK), .HRESETn(HRESETn),
    .HSELS0(fe_CPU_M_sel_shared), .HADDRS0(HADDR_CPU_M), .HTRANSS0(HTRANS_CPU_M),
    .HSIZES0(HSIZE_CPU_M), .HWRITES0(HWRITE_CPU_M), .HREADYS0(fe_CPU_M_hready),
    .HPROTS0(HPROT_CPU_M), .HBURSTS0(HBURST_CPU_M), .HMASTLOCKS0(HMASTLOCK_CPU_M & (fe_CPU_M_sel_shared | ~HTRANS_CPU_M[1])), .HWDATAS0(HWDATA_CPU_M),
    .HREADYOUTS0(s_CPU_M_hreadyout), .HRESPS0(s_CPU_M_hresp), .HRDATAS0(s_CPU_M_hrdata),
    .HSELS1(1'b1), .HADDRS1(HADDR_EXT_S), .HTRANSS1(HTRANS_EXT_S),
    .HSIZES1(HSIZE_EXT_S), .HWRITES1(HWRITE_EXT_S), .HREADYS1(s_EXT_S_hreadyout),
    .HPROTS1(HPROT_EXT_S), .HBURSTS1(HBURST_EXT_S), .HMASTLOCKS1(HMASTLOCK_EXT_S), .HWDATAS1(HWDATA_EXT_S),
    .HREADYOUTS1(s_EXT_S_hreadyout), .HRESPS1(s_EXT_S_hresp), .HRDATAS1(s_EXT_S_hrdata),
    .HSELS2(1'b0), .HADDRS2(32'h0), .HTRANSS2(TRN_IDLE), .HSIZES2(3'b000), .HWRITES2(1'b0),
    .HREADYS2(1'b1), .HPROTS2(4'b0000), .HBURSTS2(3'b000), .HMASTLOCKS2(1'b0), .HWDATAS2(32'h0),
    .HREADYOUTS2(nc_top_s2_hreadyout), .HRESPS2(nc_top_s2_hresp), .HRDATAS2(nc_top_s2_hrdata),
    .HSELM(b_hsel), .HADDRM(b_haddr), .HTRANSM(b_htrans), .HSIZEM(b_hsize),
    .HWRITEM(b_hwrite), .HREADYM(b_hready), .HPROTM(b_hprot), .HBURSTM(b_hburst),
    .HMASTLOCKM(b_hmastlock), .HWDATAM(b_hwdata),
    .HREADYOUTM(b_hreadyout), .HRESPM(b_hresp), .HRDATAM(b_hrdata),
    .HMASTERM(b_hmaster)
  );

  // ---------------------------------------------------------------------------
  // Decoder, generated from the effective address maps
  // ---------------------------------------------------------------------------
  wire [4:0] b_dec_a = b_haddr[31:27];
  wire b_dec_ROM_0 =
      (b_dec_a == 5'h0) & ~REMAP[0] |
      (b_dec_a == 5'h1);
  wire b_dec_RAM_0 =
      (b_dec_a == 5'h0) & REMAP[0] |
      (b_dec_a == 5'h2);
  wire b_dec_DAT_0 =
      (b_dec_a == 5'h3);
  wire b_dec_DEFAULT = ~(b_dec_ROM_0 | b_dec_RAM_0 | b_dec_DAT_0);
  // HSEL is a pure address decode. The master mux's HSELM qualifies the
  // transfer instead (in HTRANS below), so HSEL never toggles on its own.
  assign HSEL_ROM_0 = b_dec_ROM_0;
  assign HSEL_RAM_0 = b_dec_RAM_0;
  assign HSEL_DAT_0 = b_dec_DAT_0;
  wire   b_hsel_DEFAULT = b_dec_DEFAULT;
  wire [1:0] b_htrans_q = b_hsel ? b_htrans : TRN_IDLE;   // transfer, if a master is on the bus

  // Unmapped addresses: ERROR response
  wire   def_hreadyout;
  wire   def_hresp;
  cmsdk_ahb_default_slave u_default_slave (
    .HCLK(HCLK), .HRESETn(HRESETn), .HSEL(b_hsel_DEFAULT), .HTRANS(b_htrans_q),
    .HREADY(b_hready), .HREADYOUT(def_hreadyout), .HRESP(def_hresp)
  );

  cmsdk_ahb_slave_mux #(
    .PORT0_ENABLE(1),
    .PORT1_ENABLE(1),
    .PORT2_ENABLE(1),
    .PORT3_ENABLE(1),
    .PORT4_ENABLE(0),
    .PORT5_ENABLE(0),
    .PORT6_ENABLE(0),
    .PORT7_ENABLE(0),
    .PORT8_ENABLE(0),
    .PORT9_ENABLE(0),
    .DW(32)
  ) u_slave_mux (
    .HCLK(HCLK), .HRESETn(HRESETn), .HREADY(b_hready),
    .HSEL0(HSEL_ROM_0), .HREADYOUT0(HREADYOUT_ROM_0), .HRESP0(HRESP_ROM_0), .HRDATA0(HRDATA_ROM_0),
    .HSEL1(HSEL_RAM_0), .HREADYOUT1(HREADYOUT_RAM_0), .HRESP1(HRESP_RAM_0), .HRDATA1(HRDATA_RAM_0),
    .HSEL2(HSEL_DAT_0), .HREADYOUT2(HREADYOUT_DAT_0), .HRESP2(HRESP_DAT_0), .HRDATA2(HRDATA_DAT_0),
    .HSEL3(b_hsel_DEFAULT), .HREADYOUT3(def_hreadyout), .HRESP3(def_hresp), .HRDATA3(32'h0),
    .HSEL4(1'b0), .HREADYOUT4(1'b1), .HRESP4(1'b0), .HRDATA4(32'h0),
    .HSEL5(1'b0), .HREADYOUT5(1'b1), .HRESP5(1'b0), .HRDATA5(32'h0),
    .HSEL6(1'b0), .HREADYOUT6(1'b1), .HRESP6(1'b0), .HRDATA6(32'h0),
    .HSEL7(1'b0), .HREADYOUT7(1'b1), .HRESP7(1'b0), .HRDATA7(32'h0),
    .HSEL8(1'b0), .HREADYOUT8(1'b1), .HRESP8(1'b0), .HRDATA8(32'h0),
    .HSEL9(1'b0), .HREADYOUT9(1'b1), .HRESP9(1'b0), .HRDATA9(32'h0),
    .HREADYOUT(b_hreadyout), .HRESP(b_hresp), .HRDATA(b_hrdata)
  );

  // Target ROM_0: bus broadcast; HTRANS hidden only while another target stalls the bus
  reg    b_dp_ROM_0;                      // ROM_0 is in its data phase
  always @(posedge HCLK or negedge HRESETn)
    if (!HRESETn)       b_dp_ROM_0 <= 1'b0;
    else if (b_hready)  b_dp_ROM_0 <= HSEL_ROM_0 & HTRANS_ROM_0[1];
  assign HADDR_ROM_0     = b_haddr;
  assign HTRANS_ROM_0    = (HSEL_ROM_0 & (b_hready | b_dp_ROM_0)) ? b_htrans_q : TRN_IDLE;
  assign HWRITE_ROM_0    = b_hwrite;
  assign HSIZE_ROM_0     = b_hsize;
  assign HBURST_ROM_0    = b_hburst;
  assign HPROT_ROM_0     = b_hprot;
  assign HWDATA_ROM_0    = b_hwdata;
  assign HMASTLOCK_ROM_0 = b_hmastlock;
  assign HREADYMUX_ROM_0 = b_dp_ROM_0 ? HREADYOUT_ROM_0 : 1'b1;

  // Target RAM_0: bus broadcast; HTRANS hidden only while another target stalls the bus
  reg    b_dp_RAM_0;                      // RAM_0 is in its data phase
  always @(posedge HCLK or negedge HRESETn)
    if (!HRESETn)       b_dp_RAM_0 <= 1'b0;
    else if (b_hready)  b_dp_RAM_0 <= HSEL_RAM_0 & HTRANS_RAM_0[1];
  assign HADDR_RAM_0     = b_haddr;
  assign HTRANS_RAM_0    = (HSEL_RAM_0 & (b_hready | b_dp_RAM_0)) ? b_htrans_q : TRN_IDLE;
  assign HWRITE_RAM_0    = b_hwrite;
  assign HSIZE_RAM_0     = b_hsize;
  assign HBURST_RAM_0    = b_hburst;
  assign HPROT_RAM_0     = b_hprot;
  assign HWDATA_RAM_0    = b_hwdata;
  assign HMASTLOCK_RAM_0 = b_hmastlock;
  assign HREADYMUX_RAM_0 = b_dp_RAM_0 ? HREADYOUT_RAM_0 : 1'b1;

  // Target DAT_0: bus broadcast; HTRANS hidden only while another target stalls the bus
  reg    b_dp_DAT_0;                      // DAT_0 is in its data phase
  always @(posedge HCLK or negedge HRESETn)
    if (!HRESETn)       b_dp_DAT_0 <= 1'b0;
    else if (b_hready)  b_dp_DAT_0 <= HSEL_DAT_0 & HTRANS_DAT_0[1];
  assign HADDR_DAT_0     = b_haddr;
  assign HTRANS_DAT_0    = (HSEL_DAT_0 & (b_hready | b_dp_DAT_0)) ? b_htrans_q : TRN_IDLE;
  assign HWRITE_DAT_0    = b_hwrite;
  assign HSIZE_DAT_0     = b_hsize;
  assign HBURST_DAT_0    = b_hburst;
  assign HPROT_DAT_0     = b_hprot;
  assign HWDATA_DAT_0    = b_hwdata;
  assign HMASTLOCK_DAT_0 = b_hmastlock;
  assign HREADYMUX_DAT_0 = b_dp_DAT_0 ? HREADYOUT_DAT_0 : 1'b1;

  // No flops of this module sit on a scan chain
  assign SCANOUTHCLK = SCANINHCLK;

  // Deliberately unused
  wire unused = &{1'b0, SCANENABLE, REMAP[1], REMAP[2], REMAP[3], b_hmaster,
                     nc_top_s2_hreadyout, nc_top_s2_hresp, nc_top_s2_hrdata};

endmodule
