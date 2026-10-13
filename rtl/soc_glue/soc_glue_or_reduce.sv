//-----------------------------------------------------------------------------
// Structural OR-reduction — reduces WIDTH-bit input to 1-bit output.
// Copyright 2021-2026 SoC Labs (www.soclabs.org), University of Southampton
// SPDX-License-Identifier: Apache-2.0
//-----------------------------------------------------------------------------

module soc_glue_or_reduce #(
    parameter WIDTH = 1
) (
    input  wire [WIDTH-1:0] in,
    output wire             out
);

    assign out = |in;

endmodule
