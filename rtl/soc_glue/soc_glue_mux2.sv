//-----------------------------------------------------------------------------
// Structural 2:1 selector — picks one of two WIDTH-bit inputs at BUILD TIME.
//   out = SEL ? in1 : in0
//
// Used for build-time clock/reset source selection (e.g. a subsystem's
// CLK_RST_CONSUMER mode picking an external clock/reset tree over its own PRMU
// outputs).
//
// WHY THE SELECT IS A PARAMETER AND NOT A PORT.
// It used to be a port carrying a constant, and elaboration folded it away — so
// the netlist was already correct. But the SOURCE still read as a runtime
// multiplexer, and structural lint reads the source:
//   * HAL *E,GLTASR — "Combinatorial logic 'mux(sel)' present in the path of
//     asynchronous reset". Two of these instances select a RESET, so a lint tool
//     that does not fold the constant sees combinational logic in an async reset
//     path. That finding was noise, but it is indistinguishable from the real
//     thing (see soc_glue_and_gate, where the same rule reports a genuine
//     multi-source reset combine that cannot be removed).
//   * HAL *E,SIZMIS x3 — the select port is 1 bit and the parameter driving it
//     is an untyped Verilog parameter, i.e. 32-bit signed.
// With a generate, only the selected leg is elaborated: there is no mux, no
// select net, and no width to mismatch. The RTL now says what the design means.
//
// NETLIST-NEUTRAL: the constant was already folded, so this changes the source,
// not the hardware. Prove it with an RTL-to-RTL equivalence run rather than
// taking it on trust.
//
// If a genuine RUNTIME 2:1 mux is ever needed, add a separate cell for it —
// do not put the port back. Every instance in this design selects at build time,
// and soc_model enforces it: a mux2 glue whose select is not a literal 0/1 or a
// parameter the module declares is refused before anything is written
// (soc_model/backends/toplevel.py, mux2_select / check_mux2_selects).
//
// Copyright 2021-2026 SoC Labs (www.soclabs.org), University of Southampton
// SPDX-License-Identifier: Apache-2.0
//-----------------------------------------------------------------------------

module soc_glue_mux2 #(
    parameter WIDTH = 1,
    parameter SEL   = 0   // build-time: 0 selects in0, non-zero selects in1
) (
    input  wire [WIDTH-1:0] in0,  // selected when SEL == 0
    input  wire [WIDTH-1:0] in1,  // selected when SEL != 0
    output wire [WIDTH-1:0] out
);

    generate
        if (SEL != 0) begin : g_sel_in1
            assign out = in1;
        end else begin : g_sel_in0
            assign out = in0;
        end
    endgenerate

endmodule
