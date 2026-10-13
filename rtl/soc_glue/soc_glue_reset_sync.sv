//-----------------------------------------------------------------------------
// Structural reset synchronizer — async-assert / sync-deassert.
//
//   out (active-low resetn) follows `arstn` asynchronously when asserted
//   (arstn==0 forces out==0 immediately, no clock needed), and is RELEASED
//   synchronously to `clk` (the deassertion edge is registered through STAGES
//   flops, so the rising edge of `out` is clean and metastability-safe in the
//   `clk` domain).
//
// Why this exists:
//   When a subsystem CONSUMES an external clock/reset tree (e.g. the eth-ss
//   running in CLK_RST_CONSUMER mode off CPU1's PRMU), the consumed reset's
//   *deassertion* is asynchronous to the subsystem's own clock domain. Feeding
//   that raw reset straight into the fabric (MAC datapath, interconnect,
//   memories) lets the first active clock edge after release sample the reset
//   while it is still settling -> recovery/removal-time violation -> a
//   metastable / partially-reset datapath (observed as whole-frame payload
//   corruption on the OpenCores MAC after the di-bit assembler comes out of
//   reset). A PRMU MASTER does not need this because its rstctrl already emits
//   a reset synchronized to the clock it generates; the consumer leg loses that
//   guarantee, so it is re-synchronized here.
//
// This is a structural helper analogous to the canonical nanosoc_clkctrl /
// CMSDK reset synchronizer (3-stage shift, reset-to-0, shift-in-1).
//
// Copyright 2021-2026 SoC Labs (www.soclabs.org), University of Southampton
// SPDX-License-Identifier: Apache-2.0
//-----------------------------------------------------------------------------

module soc_glue_reset_sync #(
    parameter STAGES = 3   // number of synchroniser flops on the deassert path
) (
    input  wire clk,     // destination clock domain
    input  wire arstn,   // async active-low reset in (e.g. consumed external tree)
    output wire out      // active-low reset out, synchronised to clk
);

    reg [STAGES-1:0] sync_q;

    // Async assert (arstn==0 clears all stages immediately), sync deassert
    // (a constant 1 is shifted in on each clk edge once arstn is released).
    always @(posedge clk or negedge arstn) begin
        if (~arstn)
            sync_q <= {STAGES{1'b0}};
        else
            sync_q <= {sync_q[STAGES-2:0], 1'b1};
    end

    assign out = sync_q[STAGES-1];

endmodule
