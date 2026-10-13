# Auto-generated memory map variables
# Source:    soc_model (firmware_multicore_soc)
# Generated: 2026-06-09 19:12:04
#
# DO NOT EDIT — regenerate with gen_firmware_config.py

# Default target CPU for this SoC (used as fallback when NANOSOC_CPU is not
# passed explicitly on the make command line; see build/testcode.mk).
NANOSOC_DEFAULT_CPU := cortex-m0plus

# Memory region base addresses and sizes
ETH_SS_0_BASE  = 0x40000000
ETH_SS_0_SIZE  = 0x10000
SS_IMEM_0_BASE  = 0x50000000
SS_IMEM_0_SIZE  = 0x10000
SS_BOOTROM_0_BASE  = 0x40000000
SS_BOOTROM_0_SIZE  = 0x800
SHARED_RAM_0_BASE  = 0x18000000
SHARED_RAM_0_SIZE  = 0x2000
PERIPH_0_BASE  = 0x4000C000
PERIPH_0_SIZE  = 0x0

# Per-profile linker bases (for ARM linker --ro_base / --rw_base)
APP_ALIAS_RO_BASE = 0x40000000
APP_ALIAS_RW_BASE = 0x40000000
BOOT_REMAP_RO_BASE = 0x40000000
BOOT_REMAP_RW_BASE = 0x00000000

# objcopy --adjust-vma values
IMEM_ALIAS_ADJUST_VMA = -0x40000000
BOOT_DEFAULT_ADJUST_VMA = -0x40000000

# ARM linker options (ds5/ds6)
ARM_LINK_OPTIONS_APP_ALIAS = --rw_base $(APP_ALIAS_RW_BASE) --ro_base $(APP_ALIAS_RO_BASE) --map
ARM_LINK_OPTIONS_BOOT_REMAP = --rw_base $(BOOT_REMAP_RW_BASE) --ro_base $(BOOT_REMAP_RO_BASE) --map

# ADP upload address
ADP_UPLOAD_ADDRESS = 0x40000000
