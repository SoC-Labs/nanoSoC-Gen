//-----------------------------------------------------------------------------
// AHB Interconnect Wrapper
// - Contains AHB Lite Bus Matrix
// - Inlines AHB-to-APB bridges and APB slave muxes for APB targets
//
// Contributors
//
// David Mapstone (d.a.mapstone@soton.ac.uk)
//
// Copyright 2021-2026 SoC Labs (www.soclabs.org), University of Southampton
// SPDX-License-Identifier: Apache-2.0
//-----------------------------------------------------------------------------
// AUTO-GENERATED FILE - DO NOT EDIT DIRECTLY.
// Source:    soc_model (minimal_ahb_interconnect)
// Template:  ahb_interconnect.sv.j2
// Generated: 2026-06-09 18:54:54
//-----------------------------------------------------------------------------

module minimal_interconnect #(
    parameter    SYS_ADDR_W    = 32,  // System Address Width
    parameter    SYS_DATA_W    = 32   // System Data Width
)(
    // System Clocks, Resets and Control
    input  wire                     sys_hclk,            // AHB System Clock
    input  wire                     sys_hresetn,         // AHB System Reset
    input  wire                     sys_scanenable,      // Scan enable signal
    input  wire                     sys_scaninhclk,      // HCLK scan input  wire
    output wire                     sys_scanouthclk,     // Scan Chain Output

    // System Address Remap control
    input  wire               [3:0] sys_remap_ctrl,      // System Address REMAP control

    // cpu_m Master Port
    input  wire  [SYS_ADDR_W-1:0]  cpu_m_haddr,            // Address bus
    input  wire             [1:0]  cpu_m_htrans,           // Transfer type
    input  wire                    cpu_m_hwrite,           // Transfer direction
    input  wire             [2:0]  cpu_m_hsize,            // Transfer size
    input  wire             [2:0]  cpu_m_hburst,           // Burst type
    input  wire             [3:0]  cpu_m_hprot,            // Protection control
    input  wire  [SYS_DATA_W-1:0]  cpu_m_hwdata,           // Write data
    input  wire                    cpu_m_hmastlock,        // Locked Sequence
    output wire  [SYS_DATA_W-1:0]  cpu_m_hrdata,           // Read data bus
    output wire                    cpu_m_hready,           // HREADY feedback
    output wire                    cpu_m_hresp,            // Transfer response

    // sram_0 AHB Slave Port
    input  wire  [SYS_DATA_W-1:0]  sram_0_hrdata,          // Read data bus
    input  wire                    sram_0_hreadyout,       // HREADY feedback
    input  wire                    sram_0_hresp,           // Transfer response
    output wire                    sram_0_hsel,            // Slave Select
    output wire  [SYS_ADDR_W-1:0]  sram_0_haddr,           // Address bus
    output wire             [1:0]  sram_0_htrans,          // Transfer type
    output wire                    sram_0_hwrite,          // Transfer direction
    output wire             [2:0]  sram_0_hsize,           // Transfer size
    output wire             [2:0]  sram_0_hburst,          // Burst type
    output wire             [3:0]  sram_0_hprot,           // Protection control
    output wire  [SYS_DATA_W-1:0]  sram_0_hwdata,          // Write data
    output wire                    sram_0_hmastlock,       // Locked Sequence
    output wire                    sram_0_hreadymux        // Transfer done
);

  // -------------------------------------------------------------------------
  // Internal AHB wires for APB targets (bus matrix sees these as AHB ports)
  // -------------------------------------------------------------------------

  // -------------------------------------------------------------------------
  // Internal APB wires for APB targets
  // -------------------------------------------------------------------------

  // -------------------------------------------------------------------------
  // Bus Matrix Instantiation
  // -------------------------------------------------------------------------
  minimal_ahb_interconnect_lite u_busmatrix (
      // System Clocks, Resets and Control
      .HCLK                   (sys_hclk),            // AHB System Clock
      .HRESETn                (sys_hresetn),         // AHB System Reset
      .SCANENABLE             (sys_scanenable),      // Scan enable signal
      .SCANINHCLK             (sys_scaninhclk),      // HCLK scan input  wire
      .SCANOUTHCLK            (sys_scanouthclk),     // Scan Chain Output

      // System Address Remap control
      .REMAP                  (sys_remap_ctrl),      // System Address REMAP control

      // CPU_M Master Port
      .HADDR_CPU_M            (cpu_m_haddr),            // Address bus
      .HTRANS_CPU_M           (cpu_m_htrans),           // Transfer type
      .HWRITE_CPU_M           (cpu_m_hwrite),           // Transfer direction
      .HSIZE_CPU_M            (cpu_m_hsize),            // Transfer size
      .HBURST_CPU_M           (cpu_m_hburst),           // Burst type
      .HPROT_CPU_M            (cpu_m_hprot),            // Protection control
      .HWDATA_CPU_M           (cpu_m_hwdata),           // Write data
      .HMASTLOCK_CPU_M        (cpu_m_hmastlock),        // Locked Sequence
      .HRDATA_CPU_M           (cpu_m_hrdata),           // Read data bus
      .HREADY_CPU_M           (cpu_m_hready),           // HREADY feedback
      .HRESP_CPU_M            (cpu_m_hresp),            // Transfer response

      // SRAM_0 AHB Slave Port
      .HRDATA_SRAM_0          (sram_0_hrdata),          // Read data bus
      .HREADYOUT_SRAM_0       (sram_0_hreadyout),       // HREADY feedback
      .HRESP_SRAM_0           (sram_0_hresp),           // Transfer response
      .HSEL_SRAM_0            (sram_0_hsel),            // Slave Select
      .HADDR_SRAM_0           (sram_0_haddr),           // Address bus
      .HTRANS_SRAM_0          (sram_0_htrans),          // Transfer type
      .HWRITE_SRAM_0          (sram_0_hwrite),          // Transfer direction
      .HSIZE_SRAM_0           (sram_0_hsize),           // Transfer size
      .HBURST_SRAM_0          (sram_0_hburst),          // Burst type
      .HPROT_SRAM_0           (sram_0_hprot),           // Protection control
      .HWDATA_SRAM_0          (sram_0_hwdata),          // Write data
      .HMASTLOCK_SRAM_0       (sram_0_hmastlock),       // Locked Sequence
      .HREADYMUX_SRAM_0       (sram_0_hreadymux)        // Transfer done
  );

endmodule
