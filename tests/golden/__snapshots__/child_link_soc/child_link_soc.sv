//-----------------------------------------------------------------------------
// child_link_soc — Top-Level Structural Module
//
// Copyright 2021-2026 SoC Labs (www.soclabs.org), University of Southampton
// SPDX-License-Identifier: Apache-2.0
//-----------------------------------------------------------------------------
// AUTO-GENERATED FILE - DO NOT EDIT DIRECTLY.
// Template:  soc_toplevel.sv.j2
// Generated: 2026-09-28 09:26:12
//-----------------------------------------------------------------------------
// This module is purely structural: most combinational glue logic is expressed
// as helper module instantiations (soc_glue_*). The only exception is `concat`
// muxes, which emit a single continuous assign to pack per-bit signals into a
// multi-bit net.
//-----------------------------------------------------------------------------

module child_link_soc #(
    parameter          SYS_CLK_FREQ_HZ      = 50000000,  // System clock (50 MHz)
    parameter          SYS_ADDR_W           = 32,  // System address width
    parameter          SYS_DATA_W           = 32,  // System data width
    parameter          SRAM_RAM_ADDR_W      = 12  // Top SRAM byte-address width (4 KB)
) (
    input   wire                      sys_hclk,  // AHB clock in
    input   wire                      sys_hresetn,  // AHB reset in
    input   wire [SYS_ADDR_W-1:0]     dap_s_haddr,  // Top external slave master-in — haddr
    input   wire [1:0]                dap_s_htrans,  // Top external slave master-in — htrans
    input   wire                      dap_s_hwrite,  // Top external slave master-in — hwrite
    input   wire [2:0]                dap_s_hsize,  // Top external slave master-in — hsize
    input   wire [2:0]                dap_s_hburst,  // Top external slave master-in — hburst
    input   wire [3:0]                dap_s_hprot,  // Top external slave master-in — hprot
    input   wire [SYS_DATA_W-1:0]     dap_s_hwdata,  // Top external slave master-in — hwdata
    input   wire                      dap_s_hmastlock,  // Top external slave master-in — hmastlock
    output  wire [SYS_DATA_W-1:0]     dap_s_hrdata,  // Top external slave master-in — hrdata
    output  wire                      dap_s_hready,  // Top external slave master-in — hready
    output  wire                      dap_s_hresp  // Top external slave master-in — hresp
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
    wire [SYS_ADDR_W-1:0]     cpu_ss_0_m_haddr;  // cpu_ss_0_m AHB initiator — haddr
    wire [1:0]                cpu_ss_0_m_htrans;  // cpu_ss_0_m AHB initiator — htrans
    wire                      cpu_ss_0_m_hwrite;  // cpu_ss_0_m AHB initiator — hwrite
    wire [2:0]                cpu_ss_0_m_hsize;  // cpu_ss_0_m AHB initiator — hsize
    wire [2:0]                cpu_ss_0_m_hburst;  // cpu_ss_0_m AHB initiator — hburst
    wire [3:0]                cpu_ss_0_m_hprot;  // cpu_ss_0_m AHB initiator — hprot
    wire [SYS_DATA_W-1:0]     cpu_ss_0_m_hwdata;  // cpu_ss_0_m AHB initiator — hwdata
    wire                      cpu_ss_0_m_hmastlock;  // cpu_ss_0_m AHB initiator — hmastlock
    wire [SYS_DATA_W-1:0]     cpu_ss_0_m_hrdata;  // cpu_ss_0_m AHB initiator — hrdata
    wire                      cpu_ss_0_m_hready;  // cpu_ss_0_m AHB initiator — hready
    wire                      cpu_ss_0_m_hresp;  // cpu_ss_0_m AHB initiator — hresp
    wire                      cpu_ss_0_slave_hsel;  // cpu_ss_0_slave AHB target — hsel
    wire [SYS_ADDR_W-1:0]     cpu_ss_0_slave_haddr;  // cpu_ss_0_slave AHB target — haddr
    wire [1:0]                cpu_ss_0_slave_htrans;  // cpu_ss_0_slave AHB target — htrans
    wire                      cpu_ss_0_slave_hwrite;  // cpu_ss_0_slave AHB target — hwrite
    wire [2:0]                cpu_ss_0_slave_hsize;  // cpu_ss_0_slave AHB target — hsize
    wire [2:0]                cpu_ss_0_slave_hburst;  // cpu_ss_0_slave AHB target — hburst
    wire [3:0]                cpu_ss_0_slave_hprot;  // cpu_ss_0_slave AHB target — hprot
    wire [SYS_DATA_W-1:0]     cpu_ss_0_slave_hwdata;  // cpu_ss_0_slave AHB target — hwdata
    wire                      cpu_ss_0_slave_hmastlock;  // cpu_ss_0_slave AHB target — hmastlock
    wire                      cpu_ss_0_slave_hready;  // cpu_ss_0_slave AHB target — hready
    wire [SYS_DATA_W-1:0]     cpu_ss_0_slave_hrdata;  // cpu_ss_0_slave AHB target — hrdata
    wire                      cpu_ss_0_slave_hresp;  // cpu_ss_0_slave AHB target — hresp
    wire                      cpu_ss_0_slave_hreadyout;  // cpu_ss_0_slave AHB target — hreadyout
    wire                      cpu_ss_0_ext_hsel;  // cpu_ss_0_ext AHB target — hsel
    wire [SYS_ADDR_W-1:0]     cpu_ss_0_ext_haddr;  // cpu_ss_0_ext AHB target — haddr
    wire [1:0]                cpu_ss_0_ext_htrans;  // cpu_ss_0_ext AHB target — htrans
    wire                      cpu_ss_0_ext_hwrite;  // cpu_ss_0_ext AHB target — hwrite
    wire [2:0]                cpu_ss_0_ext_hsize;  // cpu_ss_0_ext AHB target — hsize
    wire [2:0]                cpu_ss_0_ext_hburst;  // cpu_ss_0_ext AHB target — hburst
    wire [3:0]                cpu_ss_0_ext_hprot;  // cpu_ss_0_ext AHB target — hprot
    wire [SYS_DATA_W-1:0]     cpu_ss_0_ext_hwdata;  // cpu_ss_0_ext AHB target — hwdata
    wire                      cpu_ss_0_ext_hmastlock;  // cpu_ss_0_ext AHB target — hmastlock
    wire                      cpu_ss_0_ext_hready;  // cpu_ss_0_ext AHB target — hready
    wire [SYS_DATA_W-1:0]     cpu_ss_0_ext_hrdata;  // cpu_ss_0_ext AHB target — hrdata
    wire                      cpu_ss_0_ext_hresp;  // cpu_ss_0_ext AHB target — hresp
    wire                      cpu_ss_0_ext_hreadyout;  // cpu_ss_0_ext AHB target — hreadyout
    wire [1:0]                u_cpu_ss_0_sub_s_htrans;  // cpu_ss_0_slave -> u_cpu_ss_0.sub_s — htrans qualified by hsel
    wire [1:0]                u_cpu_ss_0_ext_m_htrans;  // cpu_ss_0_ext -> u_cpu_ss_0.ext_m — htrans qualified by hsel


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

    // --- tiny_cpu_ss ---
    tiny_cpu_ss #(
        .SYS_ADDR_W          (SYS_ADDR_W),
        .SYS_DATA_W          (SYS_DATA_W),
        .IMEM_RAM_ADDR_W     (SRAM_RAM_ADDR_W)
    ) u_cpu_ss_0 (
        .sys_hclk                (sys_hclk),
        .sys_hresetn             (sys_hresetn),
        .sub_m_haddr             (cpu_ss_0_m_haddr),
        .sub_m_htrans            (cpu_ss_0_m_htrans),
        .sub_m_hwrite            (cpu_ss_0_m_hwrite),
        .sub_m_hsize             (cpu_ss_0_m_hsize),
        .sub_m_hburst            (cpu_ss_0_m_hburst),
        .sub_m_hprot             (cpu_ss_0_m_hprot),
        .sub_m_hwdata            (cpu_ss_0_m_hwdata),
        .sub_m_hmastlock         (cpu_ss_0_m_hmastlock),
        .sub_m_hrdata            (cpu_ss_0_m_hrdata),
        .sub_m_hready            (cpu_ss_0_m_hready),
        .sub_m_hresp             (cpu_ss_0_m_hresp),
        .sub_s_hsel              (cpu_ss_0_slave_hsel),
        .sub_s_haddr             (cpu_ss_0_slave_haddr),
        .sub_s_htrans            (u_cpu_ss_0_sub_s_htrans),
        .sub_s_hwrite            (cpu_ss_0_slave_hwrite),
        .sub_s_hsize             (cpu_ss_0_slave_hsize),
        .sub_s_hburst            (cpu_ss_0_slave_hburst),
        .sub_s_hprot             (cpu_ss_0_slave_hprot),
        .sub_s_hwdata            (cpu_ss_0_slave_hwdata),
        .sub_s_hmastlock         (cpu_ss_0_slave_hmastlock),
        .sub_s_hrdata            (cpu_ss_0_slave_hrdata),
        .sub_s_hresp             (cpu_ss_0_slave_hresp),
        .sub_s_hreadyout         (cpu_ss_0_slave_hreadyout),
        .ext_m_haddr             (cpu_ss_0_ext_haddr),
        .ext_m_htrans            (u_cpu_ss_0_ext_m_htrans),
        .ext_m_hwrite            (cpu_ss_0_ext_hwrite),
        .ext_m_hsize             (cpu_ss_0_ext_hsize),
        .ext_m_hburst            (cpu_ss_0_ext_hburst),
        .ext_m_hprot             (cpu_ss_0_ext_hprot),
        .ext_m_hwdata            (cpu_ss_0_ext_hwdata),
        .ext_m_hmastlock         (cpu_ss_0_ext_hmastlock),
        .ext_m_hrdata            (cpu_ss_0_ext_hrdata),
        .ext_m_hready            (cpu_ss_0_ext_hreadyout),
        .ext_m_hresp             (cpu_ss_0_ext_hresp)
    );

    //=========================================================================
    // Child Subsystem Links — HTRANS qualified by the matrix HSEL
    // (the child's own matrix ties HSEL high on its passthrough initiator port)
    //=========================================================================
    assign u_cpu_ss_0_sub_s_htrans = cpu_ss_0_slave_hsel ? cpu_ss_0_slave_htrans : 2'b00;  // cpu_ss_0_slave -> u_cpu_ss_0.sub_s (child passthrough initiator): htrans qualified by cpu_ss_0_slave_hsel
    assign u_cpu_ss_0_ext_m_htrans = cpu_ss_0_ext_hsel ? cpu_ss_0_ext_htrans : 2'b00;  // cpu_ss_0_ext -> u_cpu_ss_0.ext_m (child passthrough initiator): htrans qualified by cpu_ss_0_ext_hsel


    //=========================================================================
    // Interconnect
    //=========================================================================
    child_link_interconnect #(
        .SYS_ADDR_W    (SYS_ADDR_W),
        .SYS_DATA_W    (SYS_DATA_W)
    ) u_interconnect (
        // System clocks, resets, and control
        .sys_hclk                        (sys_hclk),
        .sys_hresetn                     (sys_hresetn),

        // dap_m Master Port
        .dap_m_haddr                     (dap_s_haddr),
        .dap_m_htrans                    (dap_s_htrans),
        .dap_m_hwrite                    (dap_s_hwrite),
        .dap_m_hsize                     (dap_s_hsize),
        .dap_m_hburst                    (dap_s_hburst),
        .dap_m_hprot                     (dap_s_hprot),
        .dap_m_hwdata                    (dap_s_hwdata),
        .dap_m_hmastlock                 (dap_s_hmastlock),
        .dap_m_hrdata                    (dap_s_hrdata),
        .dap_m_hready                    (dap_s_hready),
        .dap_m_hresp                     (dap_s_hresp),
        // cpu_ss_0_m Master Port
        .cpu_ss_0_m_haddr                (cpu_ss_0_m_haddr),
        .cpu_ss_0_m_htrans               (cpu_ss_0_m_htrans),
        .cpu_ss_0_m_hwrite               (cpu_ss_0_m_hwrite),
        .cpu_ss_0_m_hsize                (cpu_ss_0_m_hsize),
        .cpu_ss_0_m_hburst               (cpu_ss_0_m_hburst),
        .cpu_ss_0_m_hprot                (cpu_ss_0_m_hprot),
        .cpu_ss_0_m_hwdata               (cpu_ss_0_m_hwdata),
        .cpu_ss_0_m_hmastlock            (cpu_ss_0_m_hmastlock),
        .cpu_ss_0_m_hrdata               (cpu_ss_0_m_hrdata),
        .cpu_ss_0_m_hready               (cpu_ss_0_m_hready),
        .cpu_ss_0_m_hresp                (cpu_ss_0_m_hresp),
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
        // cpu_ss_0_slave Target Port
        .cpu_ss_0_slave_hsel             (cpu_ss_0_slave_hsel),
        .cpu_ss_0_slave_haddr            (cpu_ss_0_slave_haddr),
        .cpu_ss_0_slave_htrans           (cpu_ss_0_slave_htrans),
        .cpu_ss_0_slave_hwrite           (cpu_ss_0_slave_hwrite),
        .cpu_ss_0_slave_hsize            (cpu_ss_0_slave_hsize),
        .cpu_ss_0_slave_hburst           (cpu_ss_0_slave_hburst),
        .cpu_ss_0_slave_hprot            (cpu_ss_0_slave_hprot),
        .cpu_ss_0_slave_hwdata           (cpu_ss_0_slave_hwdata),
        .cpu_ss_0_slave_hmastlock        (cpu_ss_0_slave_hmastlock),
        .cpu_ss_0_slave_hreadymux        (cpu_ss_0_slave_hready),
        .cpu_ss_0_slave_hrdata           (cpu_ss_0_slave_hrdata),
        .cpu_ss_0_slave_hresp            (cpu_ss_0_slave_hresp),
        .cpu_ss_0_slave_hreadyout        (cpu_ss_0_slave_hreadyout),
        // cpu_ss_0_ext Target Port
        .cpu_ss_0_ext_hsel               (cpu_ss_0_ext_hsel),
        .cpu_ss_0_ext_haddr              (cpu_ss_0_ext_haddr),
        .cpu_ss_0_ext_htrans             (cpu_ss_0_ext_htrans),
        .cpu_ss_0_ext_hwrite             (cpu_ss_0_ext_hwrite),
        .cpu_ss_0_ext_hsize              (cpu_ss_0_ext_hsize),
        .cpu_ss_0_ext_hburst             (cpu_ss_0_ext_hburst),
        .cpu_ss_0_ext_hprot              (cpu_ss_0_ext_hprot),
        .cpu_ss_0_ext_hwdata             (cpu_ss_0_ext_hwdata),
        .cpu_ss_0_ext_hmastlock          (cpu_ss_0_ext_hmastlock),
        .cpu_ss_0_ext_hreadymux          (cpu_ss_0_ext_hready),
        .cpu_ss_0_ext_hrdata             (cpu_ss_0_ext_hrdata),
        .cpu_ss_0_ext_hresp              (cpu_ss_0_ext_hresp),
        .cpu_ss_0_ext_hreadyout          (cpu_ss_0_ext_hreadyout)
    );

endmodule
