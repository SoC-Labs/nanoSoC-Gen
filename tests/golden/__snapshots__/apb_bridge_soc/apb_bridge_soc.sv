//-----------------------------------------------------------------------------
// apb_bridge_soc — Top-Level Structural Module
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

module apb_bridge_soc #(
    parameter          SYS_CLK_FREQ_HZ      = 50000000,  // System clock (50 MHz)
    parameter          SYS_ADDR_W           = 32,  // System address width
    parameter          SYS_DATA_W           = 32,  // System data width
    parameter          APB_ADDR_W           = 12,  // APB address width
    parameter          APB_DATA_W           = 32,  // APB data width
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
    wire                      apb_periph_0_hsel;  // apb_periph_0 AHB target — hsel
    wire [SYS_ADDR_W-1:0]     apb_periph_0_haddr;  // apb_periph_0 AHB target — haddr
    wire [1:0]                apb_periph_0_htrans;  // apb_periph_0 AHB target — htrans
    wire                      apb_periph_0_hwrite;  // apb_periph_0 AHB target — hwrite
    wire [2:0]                apb_periph_0_hsize;  // apb_periph_0 AHB target — hsize
    wire [2:0]                apb_periph_0_hburst;  // apb_periph_0 AHB target — hburst
    wire [3:0]                apb_periph_0_hprot;  // apb_periph_0 AHB target — hprot
    wire [SYS_DATA_W-1:0]     apb_periph_0_hwdata;  // apb_periph_0 AHB target — hwdata
    wire                      apb_periph_0_hmastlock;  // apb_periph_0 AHB target — hmastlock
    wire                      apb_periph_0_hready;  // apb_periph_0 AHB target — hready
    wire [SYS_DATA_W-1:0]     apb_periph_0_hrdata;  // apb_periph_0 AHB target — hrdata
    wire                      apb_periph_0_hresp;  // apb_periph_0 AHB target — hresp
    wire                      apb_periph_0_hreadyout;  // apb_periph_0 AHB target — hreadyout


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

    // --- tiny_apb_periph ---
    tiny_apb_periph #(
        .SYS_ADDR_W          (SYS_ADDR_W),
        .SYS_DATA_W          (SYS_DATA_W),
        .APB_ADDR_W          (APB_ADDR_W),
        .APB_DATA_W          (APB_DATA_W)
    ) u_apb_periph_0 (
        .HCLK                    (sys_hclk),
        .HRESETn                 (sys_hresetn),
        .PCLK                    (sys_hclk),
        .PRESETn                 (sys_hresetn),
        .PCLKEN                  (1'b1),
        .HSEL                    (apb_periph_0_hsel),
        .HADDR                   (apb_periph_0_haddr),
        .HTRANS                  (apb_periph_0_htrans),
        .HWRITE                  (apb_periph_0_hwrite),
        .HSIZE                   (apb_periph_0_hsize),
        .HBURST                  (apb_periph_0_hburst),
        .HPROT                   (apb_periph_0_hprot),
        .HWDATA                  (apb_periph_0_hwdata),
        .HMASTLOCK               (apb_periph_0_hmastlock),
        .HREADY                  (apb_periph_0_hready),
        .HRDATA                  (apb_periph_0_hrdata),
        .HRESP                   (apb_periph_0_hresp),
        .HREADYOUT               (apb_periph_0_hreadyout)
    );


    //=========================================================================
    // Interconnect
    //=========================================================================
    apb_bridge_interconnect #(
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
        // apb_periph_0 Target Port
        .apb_periph_0_hsel               (apb_periph_0_hsel),
        .apb_periph_0_haddr              (apb_periph_0_haddr),
        .apb_periph_0_htrans             (apb_periph_0_htrans),
        .apb_periph_0_hwrite             (apb_periph_0_hwrite),
        .apb_periph_0_hsize              (apb_periph_0_hsize),
        .apb_periph_0_hburst             (apb_periph_0_hburst),
        .apb_periph_0_hprot              (apb_periph_0_hprot),
        .apb_periph_0_hwdata             (apb_periph_0_hwdata),
        .apb_periph_0_hmastlock          (apb_periph_0_hmastlock),
        .apb_periph_0_hreadymux          (apb_periph_0_hready),
        .apb_periph_0_hrdata             (apb_periph_0_hrdata),
        .apb_periph_0_hresp              (apb_periph_0_hresp),
        .apb_periph_0_hreadyout          (apb_periph_0_hreadyout)
    );

endmodule
