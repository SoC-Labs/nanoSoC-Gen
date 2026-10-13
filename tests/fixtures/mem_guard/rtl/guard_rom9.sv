// Guard fixture ROM, 512 words (as bootrom_gen.py -a 9 writes it).
module guard_rom9 (
    input  logic clk,
    input  logic [9-1:0] word_addr,
    output logic [32-1:0] out_data
);
endmodule
