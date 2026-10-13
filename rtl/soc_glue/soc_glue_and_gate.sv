//-----------------------------------------------------------------------------
// Structural 2-input AND gate — AND-combines two WIDTH-bit inputs.
// Copyright 2021-2026 SoC Labs (www.soclabs.org), University of Southampton
// SPDX-License-Identifier: Apache-2.0
//-----------------------------------------------------------------------------

module soc_glue_and_gate #(
    parameter WIDTH = 1
) (
    input  wire [WIDTH-1:0] in_a,
    input  wire [WIDTH-1:0] in_b,
    output wire [WIDTH-1:0] out
);

    assign out = in_a & in_b;

endmodule
