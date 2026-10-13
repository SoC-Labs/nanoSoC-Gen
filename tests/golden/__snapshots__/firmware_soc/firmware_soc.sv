//-----------------------------------------------------------------------------
// firmware_soc — Top-Level Structural Module
//
// Copyright 2021-2026 SoC Labs (www.soclabs.org), University of Southampton
// SPDX-License-Identifier: Apache-2.0
//-----------------------------------------------------------------------------
// AUTO-GENERATED FILE - DO NOT EDIT DIRECTLY.
// Template:  soc_toplevel.sv.j2
// Generated: 2026-06-09 18:53:24
//-----------------------------------------------------------------------------
// This module is purely structural: most combinational glue logic is expressed
// as helper module instantiations (soc_glue_*). The only exception is `concat`
// muxes, which emit a single continuous assign to pack per-bit signals into a
// multi-bit net.
//-----------------------------------------------------------------------------

module firmware_soc #(
    parameter          SYS_CLK_FREQ_HZ      = 25000000,  // System clock (25 MHz)
    parameter          SYS_ADDR_W           = 32,  // System address width
    parameter          SYS_DATA_W           = 32,  // System data width
    parameter          BOOTROM_RAM_ADDR_W   = 12,  // Bootrom byte-address width (4 KB)
    parameter          IMEM_RAM_ADDR_W      = 14,  // IMEM byte-address width (16 KB)
    parameter          DMEM_RAM_ADDR_W      = 14  // DMEM byte-address width (16 KB)
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
    wire                      bootrom_0_hsel;  // bootrom_0 AHB target — hsel
    wire [SYS_ADDR_W-1:0]     bootrom_0_haddr;  // bootrom_0 AHB target — haddr
    wire [1:0]                bootrom_0_htrans;  // bootrom_0 AHB target — htrans
    wire                      bootrom_0_hwrite;  // bootrom_0 AHB target — hwrite
    wire [2:0]                bootrom_0_hsize;  // bootrom_0 AHB target — hsize
    wire [2:0]                bootrom_0_hburst;  // bootrom_0 AHB target — hburst
    wire [3:0]                bootrom_0_hprot;  // bootrom_0 AHB target — hprot
    wire [SYS_DATA_W-1:0]     bootrom_0_hwdata;  // bootrom_0 AHB target — hwdata
    wire                      bootrom_0_hmastlock;  // bootrom_0 AHB target — hmastlock
    wire                      bootrom_0_hready;  // bootrom_0 AHB target — hready
    wire [SYS_DATA_W-1:0]     bootrom_0_hrdata;  // bootrom_0 AHB target — hrdata
    wire                      bootrom_0_hresp;  // bootrom_0 AHB target — hresp
    wire                      bootrom_0_hreadyout;  // bootrom_0 AHB target — hreadyout
    wire                      imem_0_hsel;  // imem_0 AHB target — hsel
    wire [SYS_ADDR_W-1:0]     imem_0_haddr;  // imem_0 AHB target — haddr
    wire [1:0]                imem_0_htrans;  // imem_0 AHB target — htrans
    wire                      imem_0_hwrite;  // imem_0 AHB target — hwrite
    wire [2:0]                imem_0_hsize;  // imem_0 AHB target — hsize
    wire [2:0]                imem_0_hburst;  // imem_0 AHB target — hburst
    wire [3:0]                imem_0_hprot;  // imem_0 AHB target — hprot
    wire [SYS_DATA_W-1:0]     imem_0_hwdata;  // imem_0 AHB target — hwdata
    wire                      imem_0_hmastlock;  // imem_0 AHB target — hmastlock
    wire                      imem_0_hready;  // imem_0 AHB target — hready
    wire [SYS_DATA_W-1:0]     imem_0_hrdata;  // imem_0 AHB target — hrdata
    wire                      imem_0_hresp;  // imem_0 AHB target — hresp
    wire                      imem_0_hreadyout;  // imem_0 AHB target — hreadyout
    wire                      dmem_0_hsel;  // dmem_0 AHB target — hsel
    wire [SYS_ADDR_W-1:0]     dmem_0_haddr;  // dmem_0 AHB target — haddr
    wire [1:0]                dmem_0_htrans;  // dmem_0 AHB target — htrans
    wire                      dmem_0_hwrite;  // dmem_0 AHB target — hwrite
    wire [2:0]                dmem_0_hsize;  // dmem_0 AHB target — hsize
    wire [2:0]                dmem_0_hburst;  // dmem_0 AHB target — hburst
    wire [3:0]                dmem_0_hprot;  // dmem_0 AHB target — hprot
    wire [SYS_DATA_W-1:0]     dmem_0_hwdata;  // dmem_0 AHB target — hwdata
    wire                      dmem_0_hmastlock;  // dmem_0 AHB target — hmastlock
    wire                      dmem_0_hready;  // dmem_0 AHB target — hready
    wire [SYS_DATA_W-1:0]     dmem_0_hrdata;  // dmem_0 AHB target — hrdata
    wire                      dmem_0_hresp;  // dmem_0 AHB target — hresp
    wire                      dmem_0_hreadyout;  // dmem_0 AHB target — hreadyout


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
        .RAM_ADDR_W          (BOOTROM_RAM_ADDR_W)
    ) u_bootrom_0 (
        .HCLK                    (sys_hclk),
        .HRESETn                 (sys_hresetn),
        .HSEL                    (bootrom_0_hsel),
        .HADDR                   (bootrom_0_haddr),
        .HTRANS                  (bootrom_0_htrans),
        .HWRITE                  (bootrom_0_hwrite),
        .HSIZE                   (bootrom_0_hsize),
        .HPROT                   (bootrom_0_hprot),
        .HWDATA                  (bootrom_0_hwdata),
        .HREADY                  (bootrom_0_hready),
        .HRDATA                  (bootrom_0_hrdata),
        .HRESP                   (bootrom_0_hresp),
        .HREADYOUT               (bootrom_0_hreadyout)
    );

    // --- tiny_region ---
    tiny_region #(
        .SYS_ADDR_W          (SYS_ADDR_W),
        .SYS_DATA_W          (SYS_DATA_W),
        .RAM_ADDR_W          (IMEM_RAM_ADDR_W)
    ) u_imem_0 (
        .HCLK                    (sys_hclk),
        .HRESETn                 (sys_hresetn),
        .HSEL                    (imem_0_hsel),
        .HADDR                   (imem_0_haddr),
        .HTRANS                  (imem_0_htrans),
        .HWRITE                  (imem_0_hwrite),
        .HSIZE                   (imem_0_hsize),
        .HPROT                   (imem_0_hprot),
        .HWDATA                  (imem_0_hwdata),
        .HREADY                  (imem_0_hready),
        .HRDATA                  (imem_0_hrdata),
        .HRESP                   (imem_0_hresp),
        .HREADYOUT               (imem_0_hreadyout)
    );

    // --- tiny_region ---
    tiny_region #(
        .SYS_ADDR_W          (SYS_ADDR_W),
        .SYS_DATA_W          (SYS_DATA_W),
        .RAM_ADDR_W          (DMEM_RAM_ADDR_W)
    ) u_dmem_0 (
        .HCLK                    (sys_hclk),
        .HRESETn                 (sys_hresetn),
        .HSEL                    (dmem_0_hsel),
        .HADDR                   (dmem_0_haddr),
        .HTRANS                  (dmem_0_htrans),
        .HWRITE                  (dmem_0_hwrite),
        .HSIZE                   (dmem_0_hsize),
        .HPROT                   (dmem_0_hprot),
        .HWDATA                  (dmem_0_hwdata),
        .HREADY                  (dmem_0_hready),
        .HRDATA                  (dmem_0_hrdata),
        .HRESP                   (dmem_0_hresp),
        .HREADYOUT               (dmem_0_hreadyout)
    );


    //=========================================================================
    // Interconnect
    //=========================================================================
    firmware_interconnect #(
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
        // bootrom_0 Target Port
        .bootrom_0_hsel                  (bootrom_0_hsel),
        .bootrom_0_haddr                 (bootrom_0_haddr),
        .bootrom_0_htrans                (bootrom_0_htrans),
        .bootrom_0_hwrite                (bootrom_0_hwrite),
        .bootrom_0_hsize                 (bootrom_0_hsize),
        .bootrom_0_hburst                (bootrom_0_hburst),
        .bootrom_0_hprot                 (bootrom_0_hprot),
        .bootrom_0_hwdata                (bootrom_0_hwdata),
        .bootrom_0_hmastlock             (bootrom_0_hmastlock),
        .bootrom_0_hreadymux             (bootrom_0_hready),
        .bootrom_0_hrdata                (bootrom_0_hrdata),
        .bootrom_0_hresp                 (bootrom_0_hresp),
        .bootrom_0_hreadyout             (bootrom_0_hreadyout),
        // imem_0 Target Port
        .imem_0_hsel                     (imem_0_hsel),
        .imem_0_haddr                    (imem_0_haddr),
        .imem_0_htrans                   (imem_0_htrans),
        .imem_0_hwrite                   (imem_0_hwrite),
        .imem_0_hsize                    (imem_0_hsize),
        .imem_0_hburst                   (imem_0_hburst),
        .imem_0_hprot                    (imem_0_hprot),
        .imem_0_hwdata                   (imem_0_hwdata),
        .imem_0_hmastlock                (imem_0_hmastlock),
        .imem_0_hreadymux                (imem_0_hready),
        .imem_0_hrdata                   (imem_0_hrdata),
        .imem_0_hresp                    (imem_0_hresp),
        .imem_0_hreadyout                (imem_0_hreadyout),
        // dmem_0 Target Port
        .dmem_0_hsel                     (dmem_0_hsel),
        .dmem_0_haddr                    (dmem_0_haddr),
        .dmem_0_htrans                   (dmem_0_htrans),
        .dmem_0_hwrite                   (dmem_0_hwrite),
        .dmem_0_hsize                    (dmem_0_hsize),
        .dmem_0_hburst                   (dmem_0_hburst),
        .dmem_0_hprot                    (dmem_0_hprot),
        .dmem_0_hwdata                   (dmem_0_hwdata),
        .dmem_0_hmastlock                (dmem_0_hmastlock),
        .dmem_0_hreadymux                (dmem_0_hready),
        .dmem_0_hrdata                   (dmem_0_hrdata),
        .dmem_0_hresp                    (dmem_0_hresp),
        .dmem_0_hreadyout                (dmem_0_hreadyout)
    );

endmodule
