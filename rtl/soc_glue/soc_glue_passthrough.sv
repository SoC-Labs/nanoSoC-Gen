//-----------------------------------------------------------------------------
// Structural passthrough — connects input directly to output.
// Synthesis-transparent: optimized to a wire by all tools.
// Copyright 2021-2026 SoC Labs (www.soclabs.org), University of Southampton
// SPDX-License-Identifier: Apache-2.0
//-----------------------------------------------------------------------------

module soc_glue_passthrough #(
    parameter WIDTH = 1
) (
    input  wire [WIDTH-1:0] in,
    output wire [WIDTH-1:0] out
);

    assign out = in;

endmodule
