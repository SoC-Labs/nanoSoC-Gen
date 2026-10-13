// Guard fixture stand-in for sl_ahb_sram: HADDR[RAM_ADDR_W-1:0], a BYTE width.
module sl_ahb_sram #(
    parameter SYS_DATA_W = 32,
    parameter RAM_ADDR_W = 14
)(
    input wire                  HCLK,
    input wire                  HRESETn,
    input wire [RAM_ADDR_W-1:0] HADDR
);
endmodule
