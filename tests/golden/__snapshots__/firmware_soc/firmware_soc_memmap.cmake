# Auto-generated CMake memory map
# Source:    soc_model (firmware_soc)
# Generated: 2026-06-09 18:52:34
#
# DO NOT EDIT — regenerate with gen_firmware_config.py
#
# Consumed by NanoSoCFirmware (firmware/cmake/NanoSoCFirmwareFunctions.cmake)
# via include() from the consumer's CMakeLists.txt. Defines per-region
# addresses and per-profile linker bases that nanosoc_add_test() reads.

# Default target CPU for this SoC (used as fallback when consumer
# CMakePresets.json doesn't set NANOSOC_CPU explicitly).
set(NanoSoC_DEFAULT_CPU "cortex-m0")

# Memory region base addresses and sizes
set(NanoSoC_REGION_BOOTROM_0_BASE 0x00000000)
set(NanoSoC_REGION_BOOTROM_0_SIZE 0x1000)
set(NanoSoC_REGION_IMEM_0_BASE 0x10000000)
set(NanoSoC_REGION_IMEM_0_SIZE 0x4000)
set(NanoSoC_REGION_DMEM_0_BASE 0x18000000)
set(NanoSoC_REGION_DMEM_0_SIZE 0x4000)

# Linker profiles available
set(NanoSoC_LINKER_PROFILES
    app_imem
    stage0_bootrom
)

# Per-profile link bases + generated MEMORY fragment
set(NanoSoC_PROFILE_app_imem_RO_BASE 0x00000000)
set(NanoSoC_PROFILE_app_imem_RW_BASE 0x18000000)
set(NanoSoC_PROFILE_app_imem_LD_FRAGMENT
    "${CMAKE_CURRENT_LIST_DIR}/firmware_soc_app_imem_memory.ld")
set(NanoSoC_PROFILE_stage0_bootrom_RO_BASE 0x00000000)
set(NanoSoC_PROFILE_stage0_bootrom_RW_BASE 0x18000000)
set(NanoSoC_PROFILE_stage0_bootrom_LD_FRAGMENT
    "${CMAKE_CURRENT_LIST_DIR}/firmware_soc_stage0_bootrom_memory.ld")

# objcopy --adjust-vma values

# ADP upload address
set(NanoSoC_ADP_UPLOAD_ADDRESS 0x10000000)
