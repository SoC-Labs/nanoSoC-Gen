/*
 * Auto-generated linker MEMORY block for stage0_bootrom
 * Source:    soc_model (firmware_soc)
 * Generated: 2026-06-09 18:52:34
 *
 * DO NOT EDIT — regenerate with gen_firmware_config.py
 */

MEMORY
{
  BOOTROM_0  (rx) : ORIGIN = 0x00000000, LENGTH = 0x1000  /* 4K */
  DMEM_0     (rwx) : ORIGIN = 0x18000000, LENGTH = 0x3C00  /* 15K */
}

/* Aliases so sections.ld / sections_bootloader.ld can use generic region names */
REGION_ALIAS("FLASH",   BOOTROM_0);
REGION_ALIAS("BOOTROM", BOOTROM_0);
REGION_ALIAS("RAM",     DMEM_0);
