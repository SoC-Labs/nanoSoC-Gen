//-----------------------------------------------------------------------------
// Structural N-input OR combiner — OR-combines N inputs of WIDTH bits each.
// Copyright 2021-2026 SoC Labs (www.soclabs.org), University of Southampton
// SPDX-License-Identifier: Apache-2.0
//-----------------------------------------------------------------------------

module soc_glue_or_combine #(
    parameter WIDTH    = 1,
    parameter N_INPUTS = 2
) (
    input  wire [N_INPUTS*WIDTH-1:0] in,
    output wire [WIDTH-1:0]          out
);

    // Unpack and OR-reduce across inputs
    integer i;
    reg [WIDTH-1:0] combined;

    always @(*) begin
        combined = {WIDTH{1'b0}};
        for (i = 0; i < N_INPUTS; i = i + 1) begin
            combined = combined | in[i*WIDTH +: WIDTH];
        end
    end

    assign out = combined;

endmodule
