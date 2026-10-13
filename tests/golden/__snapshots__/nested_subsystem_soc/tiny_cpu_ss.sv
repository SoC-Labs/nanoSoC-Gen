//-----------------------------------------------------------------------------
// tiny_cpu_ss — Top-Level Structural Module
//
// Copyright 2021-2026 SoC Labs (www.soclabs.org), University of Southampton
// SPDX-License-Identifier: Apache-2.0
//-----------------------------------------------------------------------------
// AUTO-GENERATED FILE - DO NOT EDIT DIRECTLY.
// Template:  soc_toplevel.sv.j2
// Generated: 2026-07-09 15:11:56
//-----------------------------------------------------------------------------
// This module is purely structural: most combinational glue logic is expressed
// as helper module instantiations (soc_glue_*). The only exception is `concat`
// muxes, which emit a single continuous assign to pack per-bit signals into a
// multi-bit net.
//-----------------------------------------------------------------------------

module tiny_cpu_ss #(
    parameter          SYS_ADDR_W           = 32,  // System address width
    parameter          SYS_DATA_W           = 32,  // System data width
    parameter          IMEM_RAM_ADDR_W      = 12  // Local IMEM byte-address width (4 KB)
) (
    input   wire                      sys_hclk,  // AHB clock in
    input   wire                      sys_hresetn,  // AHB reset in
    output  wire [SYS_ADDR_W-1:0]     sub_m_haddr,  // System-window passthrough master-out — haddr
    output  wire [1:0]                sub_m_htrans,  // System-window passthrough master-out — htrans
    output  wire                      sub_m_hwrite,  // System-window passthrough master-out — hwrite
    output  wire [2:0]                sub_m_hsize,  // System-window passthrough master-out — hsize
    output  wire [2:0]                sub_m_hburst,  // System-window passthrough master-out — hburst
    output  wire [3:0]                sub_m_hprot,  // System-window passthrough master-out — hprot
    output  wire [SYS_DATA_W-1:0]     sub_m_hwdata,  // System-window passthrough master-out — hwdata
    output  wire                      sub_m_hmastlock,  // System-window passthrough master-out — hmastlock
    input   wire [SYS_DATA_W-1:0]     sub_m_hrdata,  // System-window passthrough master-out — hrdata
    input   wire                      sub_m_hready,  // System-window passthrough master-out — hready
    input   wire                      sub_m_hresp,  // System-window passthrough master-out — hresp
    input   wire                      sub_s_hsel,  // External slave passthrough initiator endpoint (slave-boundary form) — hsel
    input   wire [SYS_ADDR_W-1:0]     sub_s_haddr,  // External slave passthrough initiator endpoint (slave-boundary form) — haddr
    input   wire [1:0]                sub_s_htrans,  // External slave passthrough initiator endpoint (slave-boundary form) — htrans
    input   wire                      sub_s_hwrite,  // External slave passthrough initiator endpoint (slave-boundary form) — hwrite
    input   wire [2:0]                sub_s_hsize,  // External slave passthrough initiator endpoint (slave-boundary form) — hsize
    input   wire [2:0]                sub_s_hburst,  // External slave passthrough initiator endpoint (slave-boundary form) — hburst
    input   wire [3:0]                sub_s_hprot,  // External slave passthrough initiator endpoint (slave-boundary form) — hprot
    input   wire [SYS_DATA_W-1:0]     sub_s_hwdata,  // External slave passthrough initiator endpoint (slave-boundary form) — hwdata
    input   wire                      sub_s_hmastlock,  // External slave passthrough initiator endpoint (slave-boundary form) — hmastlock
    output  wire [SYS_DATA_W-1:0]     sub_s_hrdata,  // External slave passthrough initiator endpoint (slave-boundary form) — hrdata
    output  wire                      sub_s_hresp,  // External slave passthrough initiator endpoint (slave-boundary form) — hresp
    output  wire                      sub_s_hreadyout,  // External slave passthrough initiator endpoint (slave-boundary form) — hreadyout
    input   wire [SYS_ADDR_W-1:0]     ext_m_haddr,  // External master passthrough initiator endpoint (initiator-boundary form) — haddr
    input   wire [1:0]                ext_m_htrans,  // External master passthrough initiator endpoint (initiator-boundary form) — htrans
    input   wire                      ext_m_hwrite,  // External master passthrough initiator endpoint (initiator-boundary form) — hwrite
    input   wire [2:0]                ext_m_hsize,  // External master passthrough initiator endpoint (initiator-boundary form) — hsize
    input   wire [2:0]                ext_m_hburst,  // External master passthrough initiator endpoint (initiator-boundary form) — hburst
    input   wire [3:0]                ext_m_hprot,  // External master passthrough initiator endpoint (initiator-boundary form) — hprot
    input   wire [SYS_DATA_W-1:0]     ext_m_hwdata,  // External master passthrough initiator endpoint (initiator-boundary form) — hwdata
    input   wire                      ext_m_hmastlock,  // External master passthrough initiator endpoint (initiator-boundary form) — hmastlock
    output  wire [SYS_DATA_W-1:0]     ext_m_hrdata,  // External master passthrough initiator endpoint (initiator-boundary form) — hrdata
    output  wire                      ext_m_hready,  // External master passthrough initiator endpoint (initiator-boundary form) — hready
    output  wire                      ext_m_hresp  // External master passthrough initiator endpoint (initiator-boundary form) — hresp
);

    //=========================================================================
    // Internal Wires — Shared Nets
    //=========================================================================

    //=========================================================================
    // Internal Wires — AHB Bus Bundles
    //=========================================================================
    wire [SYS_ADDR_W-1:0]     i_cpu_0_haddr;  // cpu_0 AHB initiator — haddr
    wire [1:0]                i_cpu_0_htrans;  // cpu_0 AHB initiator — htrans
    wire                      i_cpu_0_hwrite;  // cpu_0 AHB initiator — hwrite
    wire [2:0]                i_cpu_0_hsize;  // cpu_0 AHB initiator — hsize
    wire [2:0]                i_cpu_0_hburst;  // cpu_0 AHB initiator — hburst
    wire [3:0]                i_cpu_0_hprot;  // cpu_0 AHB initiator — hprot
    wire [SYS_DATA_W-1:0]     i_cpu_0_hwdata;  // cpu_0 AHB initiator — hwdata
    wire                      i_cpu_0_hmastlock;  // cpu_0 AHB initiator — hmastlock
    wire [SYS_DATA_W-1:0]     i_cpu_0_hrdata;  // cpu_0 AHB initiator — hrdata
    wire                      i_cpu_0_hready;  // cpu_0 AHB initiator — hready
    wire                      i_cpu_0_hresp;  // cpu_0 AHB initiator — hresp
    wire                      i_imem_0_hsel;  // imem_0 AHB target — hsel
    wire [SYS_ADDR_W-1:0]     i_imem_0_haddr;  // imem_0 AHB target — haddr
    wire [1:0]                i_imem_0_htrans;  // imem_0 AHB target — htrans
    wire                      i_imem_0_hwrite;  // imem_0 AHB target — hwrite
    wire [2:0]                i_imem_0_hsize;  // imem_0 AHB target — hsize
    wire [2:0]                i_imem_0_hburst;  // imem_0 AHB target — hburst
    wire [3:0]                i_imem_0_hprot;  // imem_0 AHB target — hprot
    wire [SYS_DATA_W-1:0]     i_imem_0_hwdata;  // imem_0 AHB target — hwdata
    wire                      i_imem_0_hmastlock;  // imem_0 AHB target — hmastlock
    wire                      i_imem_0_hreadymux;  // imem_0 AHB target — hreadymux
    wire [SYS_DATA_W-1:0]     i_imem_0_hrdata;  // imem_0 AHB target — hrdata
    wire                      i_imem_0_hresp;  // imem_0 AHB target — hresp
    wire                      i_imem_0_hreadyout;  // imem_0 AHB target — hreadyout
    wire                      system_hsel;  // system passthrough — hsel
    wire [SYS_ADDR_W-1:0]     system_haddr;  // system passthrough — haddr
    wire [1:0]                system_htrans;  // system passthrough — htrans
    wire                      system_hwrite;  // system passthrough — hwrite
    wire [2:0]                system_hsize;  // system passthrough — hsize
    wire [2:0]                system_hburst;  // system passthrough — hburst
    wire [3:0]                system_hprot;  // system passthrough — hprot
    wire [SYS_DATA_W-1:0]     system_hwdata;  // system passthrough — hwdata
    wire                      system_hmastlock;  // system passthrough — hmastlock
    wire [SYS_DATA_W-1:0]     system_hrdata;  // system passthrough — hrdata
    wire                      system_hresp;  // system passthrough — hresp
    wire                      system_hreadyout;  // system passthrough — hreadyout
    wire [1:0]                sub_s_htrans_gated;  // sub_s passthrough initiator — htrans qualified by hsel


    //=========================================================================
    // Glue Logic — Structural Helper Instances
    //=========================================================================

    //=========================================================================
    // Subsystem Instances
    //=========================================================================

    // --- tiny_cpu_core ---
    tiny_cpu_core u_cpu_0 (
        .HCLK                    (sys_hclk),
        .HRESETn                 (sys_hresetn),
        .HADDR                   (i_cpu_0_haddr),
        .HTRANS                  (i_cpu_0_htrans),
        .HWRITE                  (i_cpu_0_hwrite),
        .HSIZE                   (i_cpu_0_hsize),
        .HBURST                  (i_cpu_0_hburst),
        .HPROT                   (i_cpu_0_hprot),
        .HWDATA                  (i_cpu_0_hwdata),
        .HMASTLOCK               (i_cpu_0_hmastlock),
        .HRDATA                  (i_cpu_0_hrdata),
        .HREADY                  (i_cpu_0_hready),
        .HRESP                   (i_cpu_0_hresp)
    );

    // --- tiny_region ---
    tiny_region #(
        .SYS_ADDR_W          (SYS_ADDR_W),
        .SYS_DATA_W          (SYS_DATA_W),
        .RAM_ADDR_W          (IMEM_RAM_ADDR_W)
    ) u_imem_0 (
        .HCLK                    (sys_hclk),
        .HRESETn                 (sys_hresetn),
        .HSEL                    (i_imem_0_hsel),
        .HADDR                   (i_imem_0_haddr),
        .HTRANS                  (i_imem_0_htrans),
        .HWRITE                  (i_imem_0_hwrite),
        .HSIZE                   (i_imem_0_hsize),
        .HPROT                   (i_imem_0_hprot),
        .HWDATA                  (i_imem_0_hwdata),
        .HREADY                  (i_imem_0_hreadymux),
        .HRDATA                  (i_imem_0_hrdata),
        .HRESP                   (i_imem_0_hresp),
        .HREADYOUT               (i_imem_0_hreadyout)
    );

    //=========================================================================
    // Passthrough Target Default Tie-offs
    //=========================================================================
    assign sub_m_haddr = system_haddr;  // system passthrough: haddr to top-level port
    assign sub_m_htrans = system_hsel ? system_htrans : 2'b00;  // system passthrough: htrans to top-level port
    assign sub_m_hwrite = system_hwrite;  // system passthrough: hwrite to top-level port
    assign sub_m_hsize = system_hsize;  // system passthrough: hsize to top-level port
    assign sub_m_hburst = system_hburst;  // system passthrough: hburst to top-level port
    assign sub_m_hprot = system_hprot;  // system passthrough: hprot to top-level port
    assign sub_m_hwdata = system_hwdata;  // system passthrough: hwdata to top-level port
    assign sub_m_hmastlock = system_hmastlock;  // system passthrough: hmastlock to top-level port
    assign system_hrdata = sub_m_hrdata;  // system passthrough: hrdata from top-level port
    assign system_hresp = sub_m_hresp;  // system passthrough: hresp from top-level port
    assign system_hreadyout = sub_m_hready;  // system passthrough target hreadyout from sub_m_hready (initiator boundary)
    assign sub_s_htrans_gated = sub_s_hsel ? sub_s_htrans : 2'b00;  // sub_s passthrough initiator: htrans qualified by sub_s_hsel

    //=========================================================================
    // Interconnect
    //=========================================================================
    tiny_cpu_ss_interconnect #(
        .SYS_ADDR_W    (SYS_ADDR_W),
        .SYS_DATA_W    (SYS_DATA_W)
    ) u_interconnect (
        // System clocks, resets, and control
        .sys_hclk                        (sys_hclk),
        .sys_hresetn                     (sys_hresetn),
        .sys_scanouthclk                 ( ),  // unconnected

        // cpu_0 Master Port
        .cpu_0_haddr                     (i_cpu_0_haddr),
        .cpu_0_htrans                    (i_cpu_0_htrans),
        .cpu_0_hwrite                    (i_cpu_0_hwrite),
        .cpu_0_hsize                     (i_cpu_0_hsize),
        .cpu_0_hburst                    (i_cpu_0_hburst),
        .cpu_0_hprot                     (i_cpu_0_hprot),
        .cpu_0_hwdata                    (i_cpu_0_hwdata),
        .cpu_0_hmastlock                 (i_cpu_0_hmastlock),
        .cpu_0_hrdata                    (i_cpu_0_hrdata),
        .cpu_0_hready                    (i_cpu_0_hready),
        .cpu_0_hresp                     (i_cpu_0_hresp),
        // sub_s Master Port
        .sub_s_haddr                     (sub_s_haddr),
        .sub_s_htrans                    (sub_s_htrans_gated),
        .sub_s_hwrite                    (sub_s_hwrite),
        .sub_s_hsize                     (sub_s_hsize),
        .sub_s_hburst                    (sub_s_hburst),
        .sub_s_hprot                     (sub_s_hprot),
        .sub_s_hwdata                    (sub_s_hwdata),
        .sub_s_hmastlock                 (sub_s_hmastlock),
        .sub_s_hrdata                    (sub_s_hrdata),
        .sub_s_hready                    (sub_s_hreadyout),
        .sub_s_hresp                     (sub_s_hresp),
        // ext_m Master Port
        .ext_m_haddr                     (ext_m_haddr),
        .ext_m_htrans                    (ext_m_htrans),
        .ext_m_hwrite                    (ext_m_hwrite),
        .ext_m_hsize                     (ext_m_hsize),
        .ext_m_hburst                    (ext_m_hburst),
        .ext_m_hprot                     (ext_m_hprot),
        .ext_m_hwdata                    (ext_m_hwdata),
        .ext_m_hmastlock                 (ext_m_hmastlock),
        .ext_m_hrdata                    (ext_m_hrdata),
        .ext_m_hready                    (ext_m_hready),
        .ext_m_hresp                     (ext_m_hresp),
        // imem_0 Target Port
        .imem_0_hsel                     (i_imem_0_hsel),
        .imem_0_haddr                    (i_imem_0_haddr),
        .imem_0_htrans                   (i_imem_0_htrans),
        .imem_0_hwrite                   (i_imem_0_hwrite),
        .imem_0_hsize                    (i_imem_0_hsize),
        .imem_0_hburst                   (i_imem_0_hburst),
        .imem_0_hprot                    (i_imem_0_hprot),
        .imem_0_hwdata                   (i_imem_0_hwdata),
        .imem_0_hmastlock                (i_imem_0_hmastlock),
        .imem_0_hreadymux                (i_imem_0_hreadymux),
        .imem_0_hrdata                   (i_imem_0_hrdata),
        .imem_0_hresp                    (i_imem_0_hresp),
        .imem_0_hreadyout                (i_imem_0_hreadyout),
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
        .system_hreadymux                ( ),  // unused output — system interconnect drives HREADY
        .system_hrdata                   (sub_m_hrdata),
        .system_hresp                    (sub_m_hresp),
        .system_hreadyout                (sub_m_hready)
    );

endmodule
