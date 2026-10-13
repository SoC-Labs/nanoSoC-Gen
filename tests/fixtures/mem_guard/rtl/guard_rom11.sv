// Guard fixture ROM, 2048 words (as bootrom_gen.py -a 11 writes it).
module guard_rom11 (
    input  logic clk,
    input  logic [11-1:0] word_addr,
    output logic [32-1:0] out_data
);
endmodule
