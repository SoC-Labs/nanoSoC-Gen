//-----------------------------------------------------------------------------
// two_region_soc — Top-Level Structural Module
//
// Copyright 2021-2026 SoC Labs (www.soclabs.org), University of Southampton
// SPDX-License-Identifier: Apache-2.0
//-----------------------------------------------------------------------------
// AUTO-GENERATED FILE - DO NOT EDIT DIRECTLY.
// Template:  soc_toplevel.sv.j2
// Generated: 2026-06-09 18:53:23
//-----------------------------------------------------------------------------
// This module is purely structural: most combinational glue logic is expressed
// as helper module instantiations (soc_glue_*). The only exception is `concat`
// muxes, which emit a single continuous assign to pack per-bit signals into a
// multi-bit net.
//-----------------------------------------------------------------------------

module two_region_soc #(
    parameter          SYS_CLK_FREQ_HZ      = 50000000,  // System clock (50 MHz)
    parameter          SYS_ADDR_W           = 32,  // System address width
    parameter          SYS_DATA_W           = 32,  // System data width
    parameter          SRAM_RAM_ADDR_W      = 12  // SRAM byte-address width (4 KB)
) (
    input   wire                      sys_hclk,  // AHB clock in
    input   wire                      sys_hresetn,  // AHB reset in
    output  wire [SYS_ADDR_W-1:0]     cpu_m_haddr,  // CPU AHB master-out — haddr
    output  wire [1:0]                cpu_m_htrans,  // CPU AHB master-out — htrans
    output  wire                      cpu_m_hwrite,  // CPU AHB master-out — hwrite
    output  wire [2:0]                cpu_m_hsize,  // CPU AHB master-out — hsize
    output  wire [2:0]                cpu_m_hburst,  // CPU AHB master-out — hburst
    output  wire [3:0]                cpu_m_hprot,  // CPU AHB master-out — hprot
    output  wire [SYS_DATA_W-1:0]     cpu_m_hwdata,  // CPU AHB master-out — hwdata
    output  wire                      cpu_m_hmastlock,  // CPU AHB master-out — hmastlock
    input   wire [SYS_DATA_W-1:0]     cpu_m_hrdata,  // CPU AHB master-out — hrdata
    input   wire                      cpu_m_hready,  // CPU AHB master-out — hready
    input   wire                      cpu_m_hresp  // CPU AHB master-out — hresp
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
    wire                      sram_1_hsel;  // sram_1 AHB target — hsel
    wire [SYS_ADDR_W-1:0]     sram_1_haddr;  // sram_1 AHB target — haddr
    wire [1:0]                sram_1_htrans;  // sram_1 AHB target — htrans
    wire                      sram_1_hwrite;  // sram_1 AHB target — hwrite
    wire [2:0]                sram_1_hsize;  // sram_1 AHB target — hsize
    wire [2:0]                sram_1_hburst;  // sram_1 AHB target — hburst
    wire [3:0]                sram_1_hprot;  // sram_1 AHB target — hprot
    wire [SYS_DATA_W-1:0]     sram_1_hwdata;  // sram_1 AHB target — hwdata
    wire                      sram_1_hmastlock;  // sram_1 AHB target — hmastlock
    wire                      sram_1_hready;  // sram_1 AHB target — hready
    wire [SYS_DATA_W-1:0]     sram_1_hrdata;  // sram_1 AHB target — hrdata
    wire                      sram_1_hresp;  // sram_1 AHB target — hresp
    wire                      sram_1_hreadyout;  // sram_1 AHB target — hreadyout


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

    // --- tiny_region ---
    tiny_region #(
        .SYS_ADDR_W          (SYS_ADDR_W),
        .SYS_DATA_W          (SYS_DATA_W),
        .RAM_ADDR_W          (SRAM_RAM_ADDR_W)
    ) u_sram_1 (
        .HCLK                    (sys_hclk),
        .HRESETn                 (sys_hresetn),
        .HSEL                    (sram_1_hsel),
        .HADDR                   (sram_1_haddr),
        .HTRANS                  (sram_1_htrans),
        .HWRITE                  (sram_1_hwrite),
        .HSIZE                   (sram_1_hsize),
        .HPROT                   (sram_1_hprot),
        .HWDATA                  (sram_1_hwdata),
        .HREADY                  (sram_1_hready),
        .HRDATA                  (sram_1_hrdata),
        .HRESP                   (sram_1_hresp),
        .HREADYOUT               (sram_1_hreadyout)
    );


    //=========================================================================
    // Interconnect
    //=========================================================================
    two_region_interconnect #(
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
        // sram_1 Target Port
        .sram_1_hsel                     (sram_1_hsel),
        .sram_1_haddr                    (sram_1_haddr),
        .sram_1_htrans                   (sram_1_htrans),
        .sram_1_hwrite                   (sram_1_hwrite),
        .sram_1_hsize                    (sram_1_hsize),
        .sram_1_hburst                   (sram_1_hburst),
        .sram_1_hprot                    (sram_1_hprot),
        .sram_1_hwdata                   (sram_1_hwdata),
        .sram_1_hmastlock                (sram_1_hmastlock),
        .sram_1_hreadymux                (sram_1_hready),
        .sram_1_hrdata                   (sram_1_hrdata),
        .sram_1_hresp                    (sram_1_hresp),
        .sram_1_hreadyout                (sram_1_hreadyout)
    );

endmodule
