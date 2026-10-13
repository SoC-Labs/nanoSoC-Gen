# Auto-generated memory map variables
# Source:    soc_model (firmware_soc)
# Generated: 2026-06-09 18:52:34
#
# DO NOT EDIT — regenerate with gen_firmware_config.py

# Default target CPU for this SoC (used as fallback when NANOSOC_CPU is not
# passed explicitly on the make command line; see build/testcode.mk).
NANOSOC_DEFAULT_CPU := cortex-m0

# Memory region base addresses and sizes
BOOTROM_0_BASE  = 0x00000000
BOOTROM_0_SIZE  = 0x1000
IMEM_0_BASE  = 0x10000000
IMEM_0_SIZE  = 0x4000
DMEM_0_BASE  = 0x18000000
DMEM_0_SIZE  = 0x4000

# Per-profile linker bases (for ARM linker --ro_base / --rw_base)
APP_IMEM_RO_BASE = 0x00000000
APP_IMEM_RW_BASE = 0x18000000
STAGE0_BOOTROM_RO_BASE = 0x00000000
STAGE0_BOOTROM_RW_BASE = 0x18000000

# objcopy --adjust-vma values

# ARM linker options (ds5/ds6)
ARM_LINK_OPTIONS_APP_IMEM = --rw_base $(APP_IMEM_RW_BASE) --ro_base $(APP_IMEM_RO_BASE) --map
ARM_LINK_OPTIONS_STAGE0_BOOTROM = --rw_base $(STAGE0_BOOTROM_RW_BASE) --ro_base $(STAGE0_BOOTROM_RO_BASE) --map

# ADP upload address
ADP_UPLOAD_ADDRESS = 0x10000000
