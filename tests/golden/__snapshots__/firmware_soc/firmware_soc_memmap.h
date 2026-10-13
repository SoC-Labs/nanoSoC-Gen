/*
 * Auto-generated memory map header
 * Source:    soc_model (firmware_soc)
 * Generated: 2026-06-09 18:52:34
 *
 * DO NOT EDIT — regenerate with gen_firmware_config.py
 */

#ifndef FIRMWARE_SOC_MEMMAP_GENERATED_H
#define FIRMWARE_SOC_MEMMAP_GENERATED_H

/* Memory region base addresses and sizes */
#define FIRMWARE_SOC_BOOTROM_0_BASE              (0x00000000UL)  /* BOOTROM_0 (bootrom) */
#define FIRMWARE_SOC_BOOTROM_0_SIZE              (0x00001000UL)  /* BOOTROM_0 physical size */
#define FIRMWARE_SOC_IMEM_0_BASE                 (0x10000000UL)  /* IMEM_0 (None) */
#define FIRMWARE_SOC_IMEM_0_SIZE                 (0x00004000UL)  /* IMEM_0 physical size */
#define FIRMWARE_SOC_DMEM_0_BASE                 (0x18000000UL)  /* DMEM_0 (None) */
#define FIRMWARE_SOC_DMEM_0_SIZE                 (0x00004000UL)  /* DMEM_0 physical size */

/* Target configuration parameters */
/* A build may override this with -DFIRMWARE_SOC_SYS_CLK_FREQ_HZ=<value> */
#ifndef FIRMWARE_SOC_SYS_CLK_FREQ_HZ
#define FIRMWARE_SOC_SYS_CLK_FREQ_HZ             (25000000UL)  /* System clock frequency in Hz */
#endif

#endif /* FIRMWARE_SOC_MEMMAP_GENERATED_H */
