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
// Source:    soc_model (minimal_ahb_interconnect)
// Template:  config_pkg.sv.j2
// Generated: 2026-06-09 18:54:54
//-----------------------------------------------------------------------------
//
// SystemVerilog package derived from the interconnect address map YAML.
// Contains target base addresses, apertures, and user-defined config
// parameters for each target.
//
//-----------------------------------------------------------------------------

package minimal_config_pkg;

  // =========================================================================
  // Address Map — target base addresses and apertures
  // =========================================================================
  localparam logic [31:0] SRAM_0_BASE     = 32'h00000000;
  localparam logic [31:0] SRAM_0_APERTURE = 32'h00001000;

endpackage
