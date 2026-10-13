//-----------------------------------------------------------------------------
// Structural constant driver — drives a WIDTH-bit output to VALUE.
// Copyright 2021-2026 SoC Labs (www.soclabs.org), University of Southampton
// SPDX-License-Identifier: Apache-2.0
//-----------------------------------------------------------------------------

module soc_glue_constant #(
    parameter WIDTH = 1,
    parameter [WIDTH-1:0] VALUE = {WIDTH{1'b0}}
) (
    output wire [WIDTH-1:0] out
);

    assign out = VALUE;

endmodule
