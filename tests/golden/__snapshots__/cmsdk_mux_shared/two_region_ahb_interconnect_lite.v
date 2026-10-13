`timescale 1ns/1ps
//-----------------------------------------------------------------------------
// two_region_ahb_interconnect_lite -- AHB-Lite interconnect core, impl: cmsdk_mux
//
// Written by SoC Labs. Contains no Arm source. It instantiates three Arm
// Cortex-M0 Quickstart (Corstone-101) components, referenced by path through
// $(ARM_CORSTONE_101_DIR) in the interconnect filelist and never copied:
// cmsdk_ahb_master_mux, cmsdk_ahb_slave_mux, cmsdk_ahb_default_slave.
//
// Drop-in for the BuildBusMatrix-generated two_region_ahb_interconnect_lite: identical
// module name and port list.
//
// Copyright (C) 2026, SoC Labs (www.soclabs.org)
//-----------------------------------------------------------------------------
// AUTO-GENERATED FILE - DO NOT EDIT DIRECTLY.
// Source:    soc_model (two_region_ahb_interconnect)
// Template:  ahb_interconnect_cmsdk_mux_lite.v.j2
// Generated: 2026-09-26 23:46:14
//-----------------------------------------------------------------------------
// Topology
//   Shared bus: CPU_M -> S0, DMA_1 -> S1, cascade -> S2 (DMA_2 -> S0, DMA_3 -> S1)
//     of u_master_mux (S0/S1 round-robin, S2 high priority)
//     -> decoder (keyed by address-phase HMASTER) -> u_slave_mux -> SRAM_0, SRAM_1, DEFAULT
//   Each target sees the bus as a matrix port would: HSEL is a pure address
//   decode and HREADYMUX is its own view (low only in its own waited data
//   phase). HTRANS shows the bus transfer when it is addressed to the target
//   and either the bus is ready or the target is itself in the data phase
//   (then its HREADYMUX IS the bus HREADY, so a waited NONSEQ/SEQ/BUSY stays
//   visible, as AHB requires). While ANOTHER target stalls the bus the target
//   is shown IDLE: its HREADYMUX is high then, and it must not take a
//   transfer the bus has not accepted.
// Decoder: HADDR[31:12], from the effective address maps for
// no remap bits.
//-----------------------------------------------------------------------------

module two_region_ahb_interconnect_lite (
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

    // Initiator port DMA_1
    input  wire [31:0] HADDR_DMA_1,
    input  wire  [1:0] HTRANS_DMA_1,
    input  wire        HWRITE_DMA_1,
    input  wire  [2:0] HSIZE_DMA_1,
    input  wire  [2:0] HBURST_DMA_1,
    input  wire  [3:0] HPROT_DMA_1,
    input  wire [31:0] HWDATA_DMA_1,
    input  wire        HMASTLOCK_DMA_1,
    output wire [31:0] HRDATA_DMA_1,
    output wire        HREADY_DMA_1,
    output wire        HRESP_DMA_1,

    // Initiator port DMA_2
    input  wire [31:0] HADDR_DMA_2,
    input  wire  [1:0] HTRANS_DMA_2,
    input  wire        HWRITE_DMA_2,
    input  wire  [2:0] HSIZE_DMA_2,
    input  wire  [2:0] HBURST_DMA_2,
    input  wire  [3:0] HPROT_DMA_2,
    input  wire [31:0] HWDATA_DMA_2,
    input  wire        HMASTLOCK_DMA_2,
    output wire [31:0] HRDATA_DMA_2,
    output wire        HREADY_DMA_2,
    output wire        HRESP_DMA_2,

    // Initiator port DMA_3
    input  wire [31:0] HADDR_DMA_3,
    input  wire  [1:0] HTRANS_DMA_3,
    input  wire        HWRITE_DMA_3,
    input  wire  [2:0] HSIZE_DMA_3,
    input  wire  [2:0] HBURST_DMA_3,
    input  wire  [3:0] HPROT_DMA_3,
    input  wire [31:0] HWDATA_DMA_3,
    input  wire        HMASTLOCK_DMA_3,
    output wire [31:0] HRDATA_DMA_3,
    output wire        HREADY_DMA_3,
    output wire        HRESP_DMA_3,

    // Target port SRAM_0
    input  wire [31:0] HRDATA_SRAM_0,
    input  wire        HREADYOUT_SRAM_0,
    input  wire        HRESP_SRAM_0,
    output wire        HSEL_SRAM_0,
    output wire [31:0] HADDR_SRAM_0,
    output wire  [1:0] HTRANS_SRAM_0,
    output wire        HWRITE_SRAM_0,
    output wire  [2:0] HSIZE_SRAM_0,
    output wire  [2:0] HBURST_SRAM_0,
    output wire  [3:0] HPROT_SRAM_0,
    output wire [31:0] HWDATA_SRAM_0,
    output wire        HMASTLOCK_SRAM_0,
    output wire        HREADYMUX_SRAM_0,

    // Target port SRAM_1
    input  wire [31:0] HRDATA_SRAM_1,
    input  wire        HREADYOUT_SRAM_1,
    input  wire        HRESP_SRAM_1,
    output wire        HSEL_SRAM_1,
    output wire [31:0] HADDR_SRAM_1,
    output wire  [1:0] HTRANS_SRAM_1,
    output wire        HWRITE_SRAM_1,
    output wire  [2:0] HSIZE_SRAM_1,
    output wire  [2:0] HBURST_SRAM_1,
    output wire  [3:0] HPROT_SRAM_1,
    output wire [31:0] HWDATA_SRAM_1,
    output wire        HMASTLOCK_SRAM_1,
    output wire        HREADYMUX_SRAM_1,

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
  // AHB-Lite initiator: always selected, its HREADY is its own HREADYOUT
  assign HREADY_CPU_M = s_CPU_M_hreadyout;
  assign HRESP_CPU_M  = s_CPU_M_hresp;
  assign HRDATA_CPU_M = s_CPU_M_hrdata;

  // ---------------------------------------------------------------------------
  // Initiator DMA_1: shared-bus port u_master_mux.S1
  // ---------------------------------------------------------------------------
  wire                  s_DMA_1_hreadyout;   // from the master mux
  wire                  s_DMA_1_hresp;
  wire [31:0] s_DMA_1_hrdata;
  // AHB-Lite initiator: always selected, its HREADY is its own HREADYOUT
  assign HREADY_DMA_1 = s_DMA_1_hreadyout;
  assign HRESP_DMA_1  = s_DMA_1_hresp;
  assign HRDATA_DMA_1 = s_DMA_1_hrdata;

  // ---------------------------------------------------------------------------
  // Initiator DMA_2: shared-bus port u_master_mux_casc.S0
  // ---------------------------------------------------------------------------
  wire                  s_DMA_2_hreadyout;   // from the master mux
  wire                  s_DMA_2_hresp;
  wire [31:0] s_DMA_2_hrdata;
  // AHB-Lite initiator: always selected, its HREADY is its own HREADYOUT
  assign HREADY_DMA_2 = s_DMA_2_hreadyout;
  assign HRESP_DMA_2  = s_DMA_2_hresp;
  assign HRDATA_DMA_2 = s_DMA_2_hrdata;

  // ---------------------------------------------------------------------------
  // Initiator DMA_3: shared-bus port u_master_mux_casc.S1
  // ---------------------------------------------------------------------------
  wire                  s_DMA_3_hreadyout;   // from the master mux
  wire                  s_DMA_3_hresp;
  wire [31:0] s_DMA_3_hrdata;
  // AHB-Lite initiator: always selected, its HREADY is its own HREADYOUT
  assign HREADY_DMA_3 = s_DMA_3_hreadyout;
  assign HRESP_DMA_3  = s_DMA_3_hresp;
  assign HRDATA_DMA_3 = s_DMA_3_hrdata;

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

  // Cascade: a second master mux feeds u_master_mux.S2
  wire        c_hsel;
  wire [31:0] c_haddr;
  wire  [1:0] c_htrans;
  wire        c_hwrite;
  wire  [2:0] c_hsize;
  wire  [2:0] c_hburst;
  wire  [3:0] c_hprot;
  wire        c_hmastlock;
  wire [31:0] c_hwdata;
  wire        c_hready;
  wire        c_hreadyout;
  wire        c_hresp;
  wire [31:0] c_hrdata;
  wire  [1:0] c_hmaster;
  wire                  nc_casc_s2_hreadyout;   // disabled port, unused
  wire                  nc_casc_s2_hresp;
  wire [31:0] nc_casc_s2_hrdata;

  cmsdk_ahb_master_mux #(
    .PORT0_ENABLE(1), .PORT1_ENABLE(1), .PORT2_ENABLE(0), .DW(32)
  ) u_master_mux_casc (
    .HCLK(HCLK), .HRESETn(HRESETn),
    .HSELS0(1'b1), .HADDRS0(HADDR_DMA_2), .HTRANSS0(HTRANS_DMA_2),
    .HSIZES0(HSIZE_DMA_2), .HWRITES0(HWRITE_DMA_2), .HREADYS0(s_DMA_2_hreadyout),
    .HPROTS0(HPROT_DMA_2), .HBURSTS0(HBURST_DMA_2), .HMASTLOCKS0(HMASTLOCK_DMA_2), .HWDATAS0(HWDATA_DMA_2),
    .HREADYOUTS0(s_DMA_2_hreadyout), .HRESPS0(s_DMA_2_hresp), .HRDATAS0(s_DMA_2_hrdata),
    .HSELS1(1'b1), .HADDRS1(HADDR_DMA_3), .HTRANSS1(HTRANS_DMA_3),
    .HSIZES1(HSIZE_DMA_3), .HWRITES1(HWRITE_DMA_3), .HREADYS1(s_DMA_3_hreadyout),
    .HPROTS1(HPROT_DMA_3), .HBURSTS1(HBURST_DMA_3), .HMASTLOCKS1(HMASTLOCK_DMA_3), .HWDATAS1(HWDATA_DMA_3),
    .HREADYOUTS1(s_DMA_3_hreadyout), .HRESPS1(s_DMA_3_hresp), .HRDATAS1(s_DMA_3_hrdata),
    .HSELS2(1'b0), .HADDRS2(32'h0), .HTRANSS2(TRN_IDLE), .HSIZES2(3'b000), .HWRITES2(1'b0),
    .HREADYS2(1'b1), .HPROTS2(4'b0000), .HBURSTS2(3'b000), .HMASTLOCKS2(1'b0), .HWDATAS2(32'h0),
    .HREADYOUTS2(nc_casc_s2_hreadyout), .HRESPS2(nc_casc_s2_hresp), .HRDATAS2(nc_casc_s2_hrdata),
    .HSELM(c_hsel), .HADDRM(c_haddr), .HTRANSM(c_htrans), .HSIZEM(c_hsize),
    .HWRITEM(c_hwrite), .HREADYM(c_hready), .HPROTM(c_hprot), .HBURSTM(c_hburst),
    .HMASTLOCKM(c_hmastlock), .HWDATAM(c_hwdata),
    .HREADYOUTM(c_hreadyout), .HRESPM(c_hresp), .HRDATAM(c_hrdata),
    .HMASTERM(c_hmaster)
  );

  cmsdk_ahb_master_mux #(
    .PORT0_ENABLE(1), .PORT1_ENABLE(1), .PORT2_ENABLE(1), .DW(32)
  ) u_master_mux (
    .HCLK(HCLK), .HRESETn(HRESETn),
    .HSELS0(1'b1), .HADDRS0(HADDR_CPU_M), .HTRANSS0(HTRANS_CPU_M),
    .HSIZES0(HSIZE_CPU_M), .HWRITES0(HWRITE_CPU_M), .HREADYS0(s_CPU_M_hreadyout),
    .HPROTS0(HPROT_CPU_M), .HBURSTS0(HBURST_CPU_M), .HMASTLOCKS0(HMASTLOCK_CPU_M), .HWDATAS0(HWDATA_CPU_M),
    .HREADYOUTS0(s_CPU_M_hreadyout), .HRESPS0(s_CPU_M_hresp), .HRDATAS0(s_CPU_M_hrdata),
    .HSELS1(1'b1), .HADDRS1(HADDR_DMA_1), .HTRANSS1(HTRANS_DMA_1),
    .HSIZES1(HSIZE_DMA_1), .HWRITES1(HWRITE_DMA_1), .HREADYS1(s_DMA_1_hreadyout),
    .HPROTS1(HPROT_DMA_1), .HBURSTS1(HBURST_DMA_1), .HMASTLOCKS1(HMASTLOCK_DMA_1), .HWDATAS1(HWDATA_DMA_1),
    .HREADYOUTS1(s_DMA_1_hreadyout), .HRESPS1(s_DMA_1_hresp), .HRDATAS1(s_DMA_1_hrdata),
    .HSELS2(c_hsel), .HADDRS2(c_haddr), .HTRANSS2(c_htrans),
    .HSIZES2(c_hsize), .HWRITES2(c_hwrite), .HREADYS2(c_hready),
    .HPROTS2(c_hprot), .HBURSTS2(c_hburst), .HMASTLOCKS2(c_hmastlock), .HWDATAS2(c_hwdata),
    .HREADYOUTS2(c_hreadyout), .HRESPS2(c_hresp), .HRDATAS2(c_hrdata),
    .HSELM(b_hsel), .HADDRM(b_haddr), .HTRANSM(b_htrans), .HSIZEM(b_hsize),
    .HWRITEM(b_hwrite), .HREADYM(b_hready), .HPROTM(b_hprot), .HBURSTM(b_hburst),
    .HMASTLOCKM(b_hmastlock), .HWDATAM(b_hwdata),
    .HREADYOUTM(b_hreadyout), .HRESPM(b_hresp), .HRDATAM(b_hrdata),
    .HMASTERM(b_hmaster)
  );

  // ---------------------------------------------------------------------------
  // Decoder, generated from the effective address maps
  // ---------------------------------------------------------------------------
  wire [19:0] b_dec_a = b_haddr[31:12];
  // Keyed by u_master_mux's address-phase HMASTER (S0, S1, S2: the cascade)
  wire b_dec_SRAM_0 =
      (b_dec_a == 20'h0) & ((b_hmaster == 2'd0) | (b_hmaster == 2'd2));
  wire b_dec_SRAM_1 =
      (b_dec_a == 20'h10000);
  wire b_dec_DEFAULT = ~(b_dec_SRAM_0 | b_dec_SRAM_1);
  // HSEL is a pure address decode. The master mux's HSELM qualifies the
  // transfer instead (in HTRANS below), so HSEL never toggles on its own.
  assign HSEL_SRAM_0 = b_dec_SRAM_0;
  assign HSEL_SRAM_1 = b_dec_SRAM_1;
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
    .PORT3_ENABLE(0),
    .PORT4_ENABLE(0),
    .PORT5_ENABLE(0),
    .PORT6_ENABLE(0),
    .PORT7_ENABLE(0),
    .PORT8_ENABLE(0),
    .PORT9_ENABLE(0),
    .DW(32)
  ) u_slave_mux (
    .HCLK(HCLK), .HRESETn(HRESETn), .HREADY(b_hready),
    .HSEL0(HSEL_SRAM_0), .HREADYOUT0(HREADYOUT_SRAM_0), .HRESP0(HRESP_SRAM_0), .HRDATA0(HRDATA_SRAM_0),
    .HSEL1(HSEL_SRAM_1), .HREADYOUT1(HREADYOUT_SRAM_1), .HRESP1(HRESP_SRAM_1), .HRDATA1(HRDATA_SRAM_1),
    .HSEL2(b_hsel_DEFAULT), .HREADYOUT2(def_hreadyout), .HRESP2(def_hresp), .HRDATA2(32'h0),
    .HSEL3(1'b0), .HREADYOUT3(1'b1), .HRESP3(1'b0), .HRDATA3(32'h0),
    .HSEL4(1'b0), .HREADYOUT4(1'b1), .HRESP4(1'b0), .HRDATA4(32'h0),
    .HSEL5(1'b0), .HREADYOUT5(1'b1), .HRESP5(1'b0), .HRDATA5(32'h0),
    .HSEL6(1'b0), .HREADYOUT6(1'b1), .HRESP6(1'b0), .HRDATA6(32'h0),
    .HSEL7(1'b0), .HREADYOUT7(1'b1), .HRESP7(1'b0), .HRDATA7(32'h0),
    .HSEL8(1'b0), .HREADYOUT8(1'b1), .HRESP8(1'b0), .HRDATA8(32'h0),
    .HSEL9(1'b0), .HREADYOUT9(1'b1), .HRESP9(1'b0), .HRDATA9(32'h0),
    .HREADYOUT(b_hreadyout), .HRESP(b_hresp), .HRDATA(b_hrdata)
  );

  // Target SRAM_0: bus broadcast; HTRANS hidden only while another target stalls the bus
  reg    b_dp_SRAM_0;                      // SRAM_0 is in its data phase
  always @(posedge HCLK or negedge HRESETn)
    if (!HRESETn)       b_dp_SRAM_0 <= 1'b0;
    else if (b_hready)  b_dp_SRAM_0 <= HSEL_SRAM_0 & HTRANS_SRAM_0[1];
  assign HADDR_SRAM_0     = b_haddr;
  assign HTRANS_SRAM_0    = (HSEL_SRAM_0 & (b_hready | b_dp_SRAM_0)) ? b_htrans_q : TRN_IDLE;
  assign HWRITE_SRAM_0    = b_hwrite;
  assign HSIZE_SRAM_0     = b_hsize;
  assign HBURST_SRAM_0    = b_hburst;
  assign HPROT_SRAM_0     = b_hprot;
  assign HWDATA_SRAM_0    = b_hwdata;
  assign HMASTLOCK_SRAM_0 = b_hmastlock;
  assign HREADYMUX_SRAM_0 = b_dp_SRAM_0 ? HREADYOUT_SRAM_0 : 1'b1;

  // Target SRAM_1: bus broadcast; HTRANS hidden only while another target stalls the bus
  reg    b_dp_SRAM_1;                      // SRAM_1 is in its data phase
  always @(posedge HCLK or negedge HRESETn)
    if (!HRESETn)       b_dp_SRAM_1 <= 1'b0;
    else if (b_hready)  b_dp_SRAM_1 <= HSEL_SRAM_1 & HTRANS_SRAM_1[1];
  assign HADDR_SRAM_1     = b_haddr;
  assign HTRANS_SRAM_1    = (HSEL_SRAM_1 & (b_hready | b_dp_SRAM_1)) ? b_htrans_q : TRN_IDLE;
  assign HWRITE_SRAM_1    = b_hwrite;
  assign HSIZE_SRAM_1     = b_hsize;
  assign HBURST_SRAM_1    = b_hburst;
  assign HPROT_SRAM_1     = b_hprot;
  assign HWDATA_SRAM_1    = b_hwdata;
  assign HMASTLOCK_SRAM_1 = b_hmastlock;
  assign HREADYMUX_SRAM_1 = b_dp_SRAM_1 ? HREADYOUT_SRAM_1 : 1'b1;

  // No flops of this module sit on a scan chain
  assign SCANOUTHCLK = SCANINHCLK;

  // Deliberately unused
  wire unused = &{1'b0, SCANENABLE, REMAP[0], REMAP[1], REMAP[2], REMAP[3], c_hmaster,
                     nc_casc_s2_hreadyout, nc_casc_s2_hresp, nc_casc_s2_hrdata};

endmodule
