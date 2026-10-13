//-----------------------------------------------------------------------------
// passthrough_soc — Top-Level Structural Module
//
// Copyright 2021-2026 SoC Labs (www.soclabs.org), University of Southampton
// SPDX-License-Identifier: Apache-2.0
//-----------------------------------------------------------------------------
// AUTO-GENERATED FILE - DO NOT EDIT DIRECTLY.
// Template:  soc_toplevel.sv.j2
// Generated: 2026-07-09 15:11:51
//-----------------------------------------------------------------------------
// This module is purely structural: most combinational glue logic is expressed
// as helper module instantiations (soc_glue_*). The only exception is `concat`
// muxes, which emit a single continuous assign to pack per-bit signals into a
// multi-bit net.
//-----------------------------------------------------------------------------

module passthrough_soc #(
    parameter          SYS_CLK_FREQ_HZ      = 50000000,  // System clock (50 MHz)
    parameter          SYS_ADDR_W           = 32,  // System address width
    parameter          SYS_DATA_W           = 32,  // System data width
    parameter          SRAM_RAM_ADDR_W      = 12  // SRAM byte-address width (4 KB)
) (
    input   wire                      sys_hclk,  // AHB clock in
    input   wire                      sys_hresetn,  // AHB reset in
    output  wire [SYS_ADDR_W-1:0]     sys_m_haddr,  // System-window passthrough master-out — haddr
    output  wire [1:0]                sys_m_htrans,  // System-window passthrough master-out — htrans
    output  wire                      sys_m_hwrite,  // System-window passthrough master-out — hwrite
    output  wire [2:0]                sys_m_hsize,  // System-window passthrough master-out — hsize
    output  wire [2:0]                sys_m_hburst,  // System-window passthrough master-out — hburst
    output  wire [3:0]                sys_m_hprot,  // System-window passthrough master-out — hprot
    output  wire [SYS_DATA_W-1:0]     sys_m_hwdata,  // System-window passthrough master-out — hwdata
    output  wire                      sys_m_hmastlock,  // System-window passthrough master-out — hmastlock
    input   wire [SYS_DATA_W-1:0]     sys_m_hrdata,  // System-window passthrough master-out — hrdata
    input   wire                      sys_m_hready,  // System-window passthrough master-out — hready
    input   wire                      sys_m_hresp,  // System-window passthrough master-out — hresp
    input   wire                      ext_s_hsel,  // External slave passthrough initiator endpoint — hsel
    input   wire [SYS_ADDR_W-1:0]     ext_s_haddr,  // External slave passthrough initiator endpoint — haddr
    input   wire [1:0]                ext_s_htrans,  // External slave passthrough initiator endpoint — htrans
    input   wire                      ext_s_hwrite,  // External slave passthrough initiator endpoint — hwrite
    input   wire [2:0]                ext_s_hsize,  // External slave passthrough initiator endpoint — hsize
    input   wire [2:0]                ext_s_hburst,  // External slave passthrough initiator endpoint — hburst
    input   wire [3:0]                ext_s_hprot,  // External slave passthrough initiator endpoint — hprot
    input   wire [SYS_DATA_W-1:0]     ext_s_hwdata,  // External slave passthrough initiator endpoint — hwdata
    input   wire                      ext_s_hmastlock,  // External slave passthrough initiator endpoint — hmastlock
    output  wire [SYS_DATA_W-1:0]     ext_s_hrdata,  // External slave passthrough initiator endpoint — hrdata
    output  wire                      ext_s_hresp,  // External slave passthrough initiator endpoint — hresp
    output  wire                      ext_s_hreadyout  // External slave passthrough initiator endpoint — hreadyout
);

    //=========================================================================
    // Internal Wires — Shared Nets
    //=========================================================================

    //=========================================================================
    // Internal Wires — AHB Bus Bundles
    //=========================================================================
    wire                      sram_0_hsel;  // sram_0 AHB target — hsel
    wire [SYS_ADDR_W-1:0]     sram_0_haddr;  // sram_0 AHB target — haddr
    wire [1:0]                sram_0_htrans;  // sram_0 AHB target — htrans
    wire                      sram_0_hwrite;  // sram_0 AHB target — hwrite
    wire [2:0]                sram_0_hsize;  // sram_0 AHB target — hsize
    wire [2:0]                sram_0_hburst;  // sram_0 AHB target — hburst
    wire [3:0]                sram_0_hprot;  // sram_0 AHB target — hprot
    wire [SYS_DATA_W-1:0]     sram_0_hwdata;  // sram_0 AHB target — hwdata
    wire                      sram_0_hmastlock;  // sram_0 AHB target — hmastlock
    wire                      sram_0_hready;  // sram_0 AHB target — hready
    wire [SYS_DATA_W-1:0]     sram_0_hrdata;  // sram_0 AHB target — hrdata
    wire                      sram_0_hresp;  // sram_0 AHB target — hresp
    wire                      sram_0_hreadyout;  // sram_0 AHB target — hreadyout
    wire                      system_hsel;  // system AHB passthrough target — hsel
    wire [SYS_ADDR_W-1:0]     system_haddr;  // system AHB passthrough target — haddr
    wire [1:0]                system_htrans;  // system AHB passthrough target — htrans
    wire                      system_hwrite;  // system AHB passthrough target — hwrite
    wire [2:0]                system_hsize;  // system AHB passthrough target — hsize
    wire [2:0]                system_hburst;  // system AHB passthrough target — hburst
    wire [3:0]                system_hprot;  // system AHB passthrough target — hprot
    wire [SYS_DATA_W-1:0]     system_hwdata;  // system AHB passthrough target — hwdata
    wire                      system_hmastlock;  // system AHB passthrough target — hmastlock
    wire                      system_hready;  // system AHB passthrough target — hready
    wire [SYS_DATA_W-1:0]     system_hrdata;  // system AHB passthrough target — hrdata
    wire                      system_hresp;  // system AHB passthrough target — hresp
    wire                      system_hreadyout;  // system AHB passthrough target — hreadyout
    wire [1:0]                ext_s_htrans_gated;  // ext_s passthrough initiator — htrans qualified by hsel


    //=========================================================================
    // Glue Logic — Structural Helper Instances
    //=========================================================================

    //=========================================================================
    // Subsystem Instances
    //=========================================================================

    // --- tiny_region ---
    tiny_region #(
        .SYS_ADDR_W          (SYS_ADDR_W),
        .SYS_DATA_W          (SYS_DATA_W),
        .RAM_ADDR_W          (SRAM_RAM_ADDR_W)
    ) u_sram_0 (
        .HCLK                    (sys_hclk),
        .HRESETn                 (sys_hresetn),
        .HSEL                    (sram_0_hsel),
        .HADDR                   (sram_0_haddr),
        .HTRANS                  (sram_0_htrans),
        .HWRITE                  (sram_0_hwrite),
        .HSIZE                   (sram_0_hsize),
        .HPROT                   (sram_0_hprot),
        .HWDATA                  (sram_0_hwdata),
        .HREADY                  (sram_0_hready),
        .HRDATA                  (sram_0_hrdata),
        .HRESP                   (sram_0_hresp),
        .HREADYOUT               (sram_0_hreadyout)
    );

    //=========================================================================
    // Passthrough Target Default Tie-offs
    //=========================================================================
    assign sys_m_haddr = system_haddr;  // system passthrough: haddr to top-level port
    assign sys_m_htrans = system_hsel ? system_htrans : 2'b00;  // system passthrough: htrans to top-level port
    assign sys_m_hwrite = system_hwrite;  // system passthrough: hwrite to top-level port
    assign sys_m_hsize = system_hsize;  // system passthrough: hsize to top-level port
    assign sys_m_hburst = system_hburst;  // system passthrough: hburst to top-level port
    assign sys_m_hprot = system_hprot;  // system passthrough: hprot to top-level port
    assign sys_m_hwdata = system_hwdata;  // system passthrough: hwdata to top-level port
    assign sys_m_hmastlock = system_hmastlock;  // system passthrough: hmastlock to top-level port
    assign system_hrdata = sys_m_hrdata;  // system passthrough: hrdata from top-level port
    assign system_hresp = sys_m_hresp;  // system passthrough: hresp from top-level port
    assign system_hreadyout = sys_m_hready;  // system passthrough target hreadyout from sys_m_hready (initiator boundary)
    assign ext_s_htrans_gated = ext_s_hsel ? ext_s_htrans : 2'b00;  // ext_s passthrough initiator: htrans qualified by ext_s_hsel

    //=========================================================================
    // Interconnect
    //=========================================================================
    passthrough_interconnect #(
        .SYS_ADDR_W    (SYS_ADDR_W),
        .SYS_DATA_W    (SYS_DATA_W)
    ) u_interconnect (
        // System clocks, resets, and control
        .sys_hclk                        (sys_hclk),
        .sys_hresetn                     (sys_hresetn),

        // cpu_m Master Port
        .cpu_m_haddr                     (cpu_m_haddr),
        .cpu_m_htrans                    (cpu_m_htrans),
        .cpu_m_hwrite                    (cpu_m_hwrite),
        .cpu_m_hsize                     (cpu_m_hsize),
        .cpu_m_hburst                    (cpu_m_hburst),
        .cpu_m_hprot                     (cpu_m_hprot),
        .cpu_m_hwdata                    (cpu_m_hwdata),
        .cpu_m_hmastlock                 (cpu_m_hmastlock),
        .cpu_m_hrdata                    (cpu_m_hrdata),
        .cpu_m_hready                    (cpu_m_hready),
        .cpu_m_hresp                     (cpu_m_hresp),
        // ext_s Master Port
        .ext_s_haddr                     (ext_s_haddr),
        .ext_s_htrans                    (ext_s_htrans_gated),
        .ext_s_hwrite                    (ext_s_hwrite),
        .ext_s_hsize                     (ext_s_hsize),
        .ext_s_hburst                    (ext_s_hburst),
        .ext_s_hprot                     (ext_s_hprot),
        .ext_s_hwdata                    (ext_s_hwdata),
        .ext_s_hmastlock                 (ext_s_hmastlock),
        .ext_s_hrdata                    (ext_s_hrdata),
        .ext_s_hready                    (ext_s_hreadyout),
        .ext_s_hresp                     (ext_s_hresp),
        // sram_0 Target Port
        .sram_0_hsel                     (sram_0_hsel),
        .sram_0_haddr                    (sram_0_haddr),
        .sram_0_htrans                   (sram_0_htrans),
        .sram_0_hwrite                   (sram_0_hwrite),
        .sram_0_hsize                    (sram_0_hsize),
        .sram_0_hburst                   (sram_0_hburst),
        .sram_0_hprot                    (sram_0_hprot),
        .sram_0_hwdata                   (sram_0_hwdata),
        .sram_0_hmastlock                (sram_0_hmastlock),
        .sram_0_hreadymux                (sram_0_hready),
        .sram_0_hrdata                   (sram_0_hrdata),
        .sram_0_hresp                    (sram_0_hresp),
        .sram_0_hreadyout                (sram_0_hreadyout),
        // system Target Port
        .system_hsel                     (system_hsel),
        .system_haddr                    (system_haddr),
        .system_htrans                   (system_htrans),
        .system_hwrite                   (system_hwrite),
        .system_hsize                    (system_hsize),
        .system_hburst                   (system_hburst),
        .system_hprot                    (system_hprot),
        .system_hwdata                   (system_hwdata),
        .system_hmastlock                (system_hmastlock),
        .system_hreadymux                (system_hready),
        .system_hrdata                   (system_hrdata),
        .system_hresp                    (system_hresp),
        .system_hreadyout                (system_hreadyout)
    );

endmodule
