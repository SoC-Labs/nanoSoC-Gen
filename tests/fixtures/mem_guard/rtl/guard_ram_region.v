// Guard fixture: a RAM region, sl_ahb_sram behind it (as nanosoc_region_sram).
module guard_ram_region #(
    parameter SYS_ADDR_W = 32,
    parameter SYS_DATA_W = 32,
    parameter RAM_ADDR_W = 12
)(
    input  wire                  HCLK,
    input  wire                  HRESETn,
    input  wire [SYS_ADDR_W-1:0] HADDR
);
    sl_ahb_sram #(
        .SYS_DATA_W (SYS_DATA_W),
        .RAM_ADDR_W (RAM_ADDR_W)
    ) u_sram (
        .HCLK    (HCLK),
        .HRESETn (HRESETn),
        .HADDR   (HADDR[RAM_ADDR_W-1:0])
    );
endmodule
