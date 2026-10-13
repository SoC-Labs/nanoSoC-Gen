# Auto-generated CMake memory map
# Source:    soc_model (firmware_multicore_soc)
# Generated: 2026-06-09 19:12:04
#
# DO NOT EDIT — regenerate with gen_firmware_config.py
#
# Consumed by NanoSoCFirmware (firmware/cmake/NanoSoCFirmwareFunctions.cmake)
# via include() from the consumer's CMakeLists.txt. Defines per-region
# addresses and per-profile linker bases that nanosoc_add_test() reads.

# Default target CPU for this SoC (used as fallback when consumer
# CMakePresets.json doesn't set NANOSOC_CPU explicitly).
set(NanoSoC_DEFAULT_CPU "cortex-m0plus")

# Memory region base addresses and sizes
set(NanoSoC_REGION_ETH_SS_0_BASE 0x40000000)
set(NanoSoC_REGION_ETH_SS_0_SIZE 0x10000)
set(NanoSoC_REGION_SS_IMEM_0_BASE 0x50000000)
set(NanoSoC_REGION_SS_IMEM_0_SIZE 0x10000)
set(NanoSoC_REGION_SS_BOOTROM_0_BASE 0x40000000)
set(NanoSoC_REGION_SS_BOOTROM_0_SIZE 0x800)
set(NanoSoC_REGION_SHARED_RAM_0_BASE 0x18000000)
set(NanoSoC_REGION_SHARED_RAM_0_SIZE 0x2000)
set(NanoSoC_REGION_PERIPH_0_BASE 0x4000C000)
set(NanoSoC_REGION_PERIPH_0_SIZE 0x0)

# Linker profiles available
set(NanoSoC_LINKER_PROFILES
    app_alias
    boot_remap
)

# Per-profile link bases + generated MEMORY fragment
set(NanoSoC_PROFILE_app_alias_RO_BASE 0x40000000)
set(NanoSoC_PROFILE_app_alias_RW_BASE 0x40000000)
set(NanoSoC_PROFILE_app_alias_LD_FRAGMENT
    "${CMAKE_CURRENT_LIST_DIR}/firmware_multicore_soc_app_alias_memory.ld")
set(NanoSoC_PROFILE_boot_remap_RO_BASE 0x40000000)
set(NanoSoC_PROFILE_boot_remap_RW_BASE 0x00000000)
set(NanoSoC_PROFILE_boot_remap_LD_FRAGMENT
    "${CMAKE_CURRENT_LIST_DIR}/firmware_multicore_soc_boot_remap_memory.ld")

# objcopy --adjust-vma values
set(NanoSoC_HEX_ADJUST_imem_alias -0x40000000)
set(NanoSoC_HEX_ADJUST_boot_default -0x40000000)

# ADP upload address
set(NanoSoC_ADP_UPLOAD_ADDRESS 0x40000000)
