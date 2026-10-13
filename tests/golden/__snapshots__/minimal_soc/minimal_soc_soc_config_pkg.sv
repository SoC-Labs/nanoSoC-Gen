//-----------------------------------------------------------------------------
// SoC Configuration Package
//
// Contributors
//
// David Mapstone (d.a.mapstone@soton.ac.uk)
//
// Copyright 2021-2026 SoC Labs (www.soclabs.org), University of Southampton
// SPDX-License-Identifier: Apache-2.0
//-----------------------------------------------------------------------------
// AUTO-GENERATED FILE - DO NOT EDIT DIRECTLY.
// Source:    minimal_soc.yaml
// Template:  nanosoc_soc_config_pkg.sv.j2
// Generated: 2026-06-09 18:56:10
//-----------------------------------------------------------------------------
//
// SystemVerilog package containing all SoC design parameters.
// Replaces gen_defines.v as the single source of configuration truth.
// Parameters that cannot be expressed as SV params (e.g. POWER_PINS,
// CORTEX_M0) remain as compiler +define+ flags.
//
//-----------------------------------------------------------------------------

package minimal_soc_soc_config_pkg;

  // =========================================================================
  // Design Configuration — from YAML system description
  // =========================================================================
  localparam int     SYS_ADDR_W                   = 32;  // System address width
  localparam int     SYS_DATA_W                   = 32;  // System data width

  // =========================================================================
  // Simulation Configuration — testbench parameters
  // =========================================================================
  localparam int     FAST_LOAD                    = 0;  // Fast memory load mode (1=fast, 0=normal)
  localparam int     VCD_ENABLE                   = 0;  // VCD waveform dump enable (1=enabled, 0=disabled)

endpackage
