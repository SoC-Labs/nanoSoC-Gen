/*
 * Auto-generated memory map header
 * Source:    soc_model (firmware_multicore_soc)
 * Generated: 2026-06-09 19:12:04
 *
 * DO NOT EDIT — regenerate with gen_firmware_config.py
 */

#ifndef FIRMWARE_MULTICORE_SOC_MEMMAP_GENERATED_H
#define FIRMWARE_MULTICORE_SOC_MEMMAP_GENERATED_H

/* Memory region base addresses and sizes */
#define FIRMWARE_MULTICORE_SOC_ETH_SS_0_BASE     (0x40000000UL)  /* ETH_SS_0 (None) */
#define FIRMWARE_MULTICORE_SOC_ETH_SS_0_SIZE     (0x00010000UL)  /* ETH_SS_0 physical size */
#define FIRMWARE_MULTICORE_SOC_SS_IMEM_0_BASE    (0x50000000UL)  /* SS_IMEM_0 (None) */
#define FIRMWARE_MULTICORE_SOC_SS_IMEM_0_SIZE    (0x00010000UL)  /* SS_IMEM_0 physical size */
#define FIRMWARE_MULTICORE_SOC_SS_IMEM_0_ALIAS   (0x40000000UL)  /* SS_IMEM_0 alias */
#define FIRMWARE_MULTICORE_SOC_SS_IMEM_0_REMAP   (0x40000000UL)  /* SS_IMEM_0 remapped */
#define FIRMWARE_MULTICORE_SOC_SS_BOOTROM_0_BASE (0x40000000UL)  /* SS_BOOTROM_0 (bootrom) */
#define FIRMWARE_MULTICORE_SOC_SS_BOOTROM_0_SIZE (0x00000800UL)  /* SS_BOOTROM_0 physical size */
#define FIRMWARE_MULTICORE_SOC_SHARED_RAM_0_BASE (0x18000000UL)  /* SHARED_RAM_0 (None) */
#define FIRMWARE_MULTICORE_SOC_SHARED_RAM_0_SIZE (0x00002000UL)  /* SHARED_RAM_0 physical size */
#define FIRMWARE_MULTICORE_SOC_PERIPH_0_BASE     (0x4000C000UL)  /* PERIPH_0 (peripheral) */

/* Target configuration parameters */
/* A build may override this with -DFIRMWARE_MULTICORE_SOC_SYS_CLK_FREQ_HZ=<value> */
#ifndef FIRMWARE_MULTICORE_SOC_SYS_CLK_FREQ_HZ
#define FIRMWARE_MULTICORE_SOC_SYS_CLK_FREQ_HZ   (50000000UL)  /* System clock frequency in Hz */
#endif

#endif /* FIRMWARE_MULTICORE_SOC_MEMMAP_GENERATED_H */
