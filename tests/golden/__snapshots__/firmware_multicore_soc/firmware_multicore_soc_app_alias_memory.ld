/*
 * Auto-generated linker MEMORY block for app_alias
 * Source:    soc_model (firmware_multicore_soc)
 * Generated: 2026-06-09 19:12:04
 *
 * DO NOT EDIT — regenerate with gen_firmware_config.py
 */

MEMORY
{
  APP_IMEM   (rwx) : ORIGIN = 0x40000000, LENGTH = 0x10000  /* 64K */
  SS_BOOTROM_0 (rx) : ORIGIN = 0x40000000, LENGTH = 0x800  /* 2K */
}

/* Aliases so sections.ld / sections_bootloader.ld can use generic region names */
REGION_ALIAS("FLASH",   SS_BOOTROM_0);
REGION_ALIAS("BOOTROM", SS_BOOTROM_0);
REGION_ALIAS("RAM",     APP_IMEM);
