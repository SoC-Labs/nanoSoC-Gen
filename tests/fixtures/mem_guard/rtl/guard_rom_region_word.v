// Guard fixture boot ROM wrapper, bootrom_gen.py form: HADDR[ROM_ADDR_W+1:2],
// ROM_ADDR_W a WORD width (4 * 2 ** ROM_ADDR_W bytes).
module guard_rom_region #(
    parameter SYS_ADDR_W = 32,
    parameter SYS_DATA_W = 32,
    parameter ROM_ADDR_W = 11
)(
    input  wire                  HCLK,
    input  wire [SYS_ADDR_W-1:0] HADDR,
    output wire [SYS_DATA_W-1:0] HRDATA
);
    wire [ROM_ADDR_W-1:0] rom_word_addr = HADDR[ROM_ADDR_W+1:2];
    guard_rom11 u_bootrom (
        .clk       (HCLK),
        .word_addr (rom_word_addr),
        .out_data  (HRDATA)
    );
endmodule
