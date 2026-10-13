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
// Source:    soc_model (cmsdk_mux_ahb_interconnect)
// Template:  ahb_interconnect.sv.j2
// Generated: 2026-09-26 17:40:10
//-----------------------------------------------------------------------------

module cmsdk_mux_interconnect #(
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

    // ext_s Master Port
    input  wire  [SYS_ADDR_W-1:0]  ext_s_haddr,            // Address bus
    input  wire             [1:0]  ext_s_htrans,           // Transfer type
    input  wire                    ext_s_hwrite,           // Transfer direction
    input  wire             [2:0]  ext_s_hsize,            // Transfer size
    input  wire             [2:0]  ext_s_hburst,           // Burst type
    input  wire             [3:0]  ext_s_hprot,            // Protection control
    input  wire  [SYS_DATA_W-1:0]  ext_s_hwdata,           // Write data
    input  wire                    ext_s_hmastlock,        // Locked Sequence
    output wire  [SYS_DATA_W-1:0]  ext_s_hrdata,           // Read data bus
    output wire                    ext_s_hready,           // HREADY feedback
    output wire                    ext_s_hresp,            // Transfer response

    // rom_0 AHB Slave Port
    input  wire  [SYS_DATA_W-1:0]  rom_0_hrdata,           // Read data bus
    input  wire                    rom_0_hreadyout,        // HREADY feedback
    input  wire                    rom_0_hresp,            // Transfer response
    output wire                    rom_0_hsel,             // Slave Select
    output wire  [SYS_ADDR_W-1:0]  rom_0_haddr,            // Address bus
    output wire             [1:0]  rom_0_htrans,           // Transfer type
    output wire                    rom_0_hwrite,           // Transfer direction
    output wire             [2:0]  rom_0_hsize,            // Transfer size
    output wire             [2:0]  rom_0_hburst,           // Burst type
    output wire             [3:0]  rom_0_hprot,            // Protection control
    output wire  [SYS_DATA_W-1:0]  rom_0_hwdata,           // Write data
    output wire                    rom_0_hmastlock,        // Locked Sequence
    output wire                    rom_0_hreadymux,        // Transfer done

    // ram_0 AHB Slave Port
    input  wire  [SYS_DATA_W-1:0]  ram_0_hrdata,           // Read data bus
    input  wire                    ram_0_hreadyout,        // HREADY feedback
    input  wire                    ram_0_hresp,            // Transfer response
    output wire                    ram_0_hsel,             // Slave Select
    output wire  [SYS_ADDR_W-1:0]  ram_0_haddr,            // Address bus
    output wire             [1:0]  ram_0_htrans,           // Transfer type
    output wire                    ram_0_hwrite,           // Transfer direction
    output wire             [2:0]  ram_0_hsize,            // Transfer size
    output wire             [2:0]  ram_0_hburst,           // Burst type
    output wire             [3:0]  ram_0_hprot,            // Protection control
    output wire  [SYS_DATA_W-1:0]  ram_0_hwdata,           // Write data
    output wire                    ram_0_hmastlock,        // Locked Sequence
    output wire                    ram_0_hreadymux,        // Transfer done

    // dat_0 AHB Slave Port
    input  wire  [SYS_DATA_W-1:0]  dat_0_hrdata,           // Read data bus
    input  wire                    dat_0_hreadyout,        // HREADY feedback
    input  wire                    dat_0_hresp,            // Transfer response
    output wire                    dat_0_hsel,             // Slave Select
    output wire  [SYS_ADDR_W-1:0]  dat_0_haddr,            // Address bus
    output wire             [1:0]  dat_0_htrans,           // Transfer type
    output wire                    dat_0_hwrite,           // Transfer direction
    output wire             [2:0]  dat_0_hsize,            // Transfer size
    output wire             [2:0]  dat_0_hburst,           // Burst type
    output wire             [3:0]  dat_0_hprot,            // Protection control
    output wire  [SYS_DATA_W-1:0]  dat_0_hwdata,           // Write data
    output wire                    dat_0_hmastlock,        // Locked Sequence
    output wire                    dat_0_hreadymux,        // Transfer done

    // system AHB Slave Port
    input  wire  [SYS_DATA_W-1:0]  system_hrdata,          // Read data bus
    input  wire                    system_hreadyout,       // HREADY feedback
    input  wire                    system_hresp,           // Transfer response
    output wire                    system_hsel,            // Slave Select
    output wire  [SYS_ADDR_W-1:0]  system_haddr,           // Address bus
    output wire             [1:0]  system_htrans,          // Transfer type
    output wire                    system_hwrite,          // Transfer direction
    output wire             [2:0]  system_hsize,           // Transfer size
    output wire             [2:0]  system_hburst,          // Burst type
    output wire             [3:0]  system_hprot,           // Protection control
    output wire  [SYS_DATA_W-1:0]  system_hwdata,          // Write data
    output wire                    system_hmastlock,       // Locked Sequence
    output wire                    system_hreadymux        // Transfer done
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
  cmsdk_mux_ahb_interconnect_lite u_busmatrix (
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

      // EXT_S Master Port
      .HADDR_EXT_S            (ext_s_haddr),            // Address bus
      .HTRANS_EXT_S           (ext_s_htrans),           // Transfer type
      .HWRITE_EXT_S           (ext_s_hwrite),           // Transfer direction
      .HSIZE_EXT_S            (ext_s_hsize),            // Transfer size
      .HBURST_EXT_S           (ext_s_hburst),           // Burst type
      .HPROT_EXT_S            (ext_s_hprot),            // Protection control
      .HWDATA_EXT_S           (ext_s_hwdata),           // Write data
      .HMASTLOCK_EXT_S        (ext_s_hmastlock),        // Locked Sequence
      .HRDATA_EXT_S           (ext_s_hrdata),           // Read data bus
      .HREADY_EXT_S           (ext_s_hready),           // HREADY feedback
      .HRESP_EXT_S            (ext_s_hresp),            // Transfer response

      // ROM_0 AHB Slave Port
      .HRDATA_ROM_0           (rom_0_hrdata),           // Read data bus
      .HREADYOUT_ROM_0        (rom_0_hreadyout),        // HREADY feedback
      .HRESP_ROM_0            (rom_0_hresp),            // Transfer response
      .HSEL_ROM_0             (rom_0_hsel),             // Slave Select
      .HADDR_ROM_0            (rom_0_haddr),            // Address bus
      .HTRANS_ROM_0           (rom_0_htrans),           // Transfer type
      .HWRITE_ROM_0           (rom_0_hwrite),           // Transfer direction
      .HSIZE_ROM_0            (rom_0_hsize),            // Transfer size
      .HBURST_ROM_0           (rom_0_hburst),           // Burst type
      .HPROT_ROM_0            (rom_0_hprot),            // Protection control
      .HWDATA_ROM_0           (rom_0_hwdata),           // Write data
      .HMASTLOCK_ROM_0        (rom_0_hmastlock),        // Locked Sequence
      .HREADYMUX_ROM_0        (rom_0_hreadymux),        // Transfer done

      // RAM_0 AHB Slave Port
      .HRDATA_RAM_0           (ram_0_hrdata),           // Read data bus
      .HREADYOUT_RAM_0        (ram_0_hreadyout),        // HREADY feedback
      .HRESP_RAM_0            (ram_0_hresp),            // Transfer response
      .HSEL_RAM_0             (ram_0_hsel),             // Slave Select
      .HADDR_RAM_0            (ram_0_haddr),            // Address bus
      .HTRANS_RAM_0           (ram_0_htrans),           // Transfer type
      .HWRITE_RAM_0           (ram_0_hwrite),           // Transfer direction
      .HSIZE_RAM_0            (ram_0_hsize),            // Transfer size
      .HBURST_RAM_0           (ram_0_hburst),           // Burst type
      .HPROT_RAM_0            (ram_0_hprot),            // Protection control
      .HWDATA_RAM_0           (ram_0_hwdata),           // Write data
      .HMASTLOCK_RAM_0        (ram_0_hmastlock),        // Locked Sequence
      .HREADYMUX_RAM_0        (ram_0_hreadymux),        // Transfer done

      // DAT_0 AHB Slave Port
      .HRDATA_DAT_0           (dat_0_hrdata),           // Read data bus
      .HREADYOUT_DAT_0        (dat_0_hreadyout),        // HREADY feedback
      .HRESP_DAT_0            (dat_0_hresp),            // Transfer response
      .HSEL_DAT_0             (dat_0_hsel),             // Slave Select
      .HADDR_DAT_0            (dat_0_haddr),            // Address bus
      .HTRANS_DAT_0           (dat_0_htrans),           // Transfer type
      .HWRITE_DAT_0           (dat_0_hwrite),           // Transfer direction
      .HSIZE_DAT_0            (dat_0_hsize),            // Transfer size
      .HBURST_DAT_0           (dat_0_hburst),           // Burst type
      .HPROT_DAT_0            (dat_0_hprot),            // Protection control
      .HWDATA_DAT_0           (dat_0_hwdata),           // Write data
      .HMASTLOCK_DAT_0        (dat_0_hmastlock),        // Locked Sequence
      .HREADYMUX_DAT_0        (dat_0_hreadymux),        // Transfer done

      // SYSTEM AHB Slave Port
      .HRDATA_SYSTEM          (system_hrdata),          // Read data bus
      .HREADYOUT_SYSTEM       (system_hreadyout),       // HREADY feedback
      .HRESP_SYSTEM           (system_hresp),           // Transfer response
      .HSEL_SYSTEM            (system_hsel),            // Slave Select
      .HADDR_SYSTEM           (system_haddr),           // Address bus
      .HTRANS_SYSTEM          (system_htrans),          // Transfer type
      .HWRITE_SYSTEM          (system_hwrite),          // Transfer direction
      .HSIZE_SYSTEM           (system_hsize),           // Transfer size
      .HBURST_SYSTEM          (system_hburst),          // Burst type
      .HPROT_SYSTEM           (system_hprot),           // Protection control
      .HWDATA_SYSTEM          (system_hwdata),          // Write data
      .HMASTLOCK_SYSTEM       (system_hmastlock),       // Locked Sequence
      .HREADYMUX_SYSTEM       (system_hreadymux)        // Transfer done
  );

endmodule
