// Guard fixture boot ROM wrapper, hand-written form: HADDR[ROM_ADDR_W-1:2],
// ROM_ADDR_W a BYTE width (2 ** ROM_ADDR_W bytes).
module guard_rom_region #(
    parameter SYS_ADDR_W = 32,
    parameter SYS_DATA_W = 32,
    parameter ROM_ADDR_W = 11
)(
    input  wire                  HCLK,
    input  wire [SYS_ADDR_W-1:0] HADDR,
    output wire [SYS_DATA_W-1:0] HRDATA
);
    guard_rom9 u_bootrom (
        .clk       (HCLK),
        .word_addr (HADDR[ROM_ADDR_W-1:2]),
        .out_data  (HRDATA)
    );
endmodule
