"""Memory map configuration and known constants for NanoSoC debug analysis."""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional


class MemoryRegion:
    """A memory region with base address, size, and access permissions."""

    def __init__(self, name, base, size, access='RW', aliases=None, remap_to=None):
        self.name = name
        self.base = base
        self.size = size
        self.access = access  # 'RW', 'RO', 'RX', 'RWX'
        self.aliases = aliases or []
        self.remap_to = remap_to

    @property
    def end(self):
        return self.base + self.size

    def contains(self, addr):
        if self.base <= addr < self.end:
            return True
        for alias in self.aliases:
            if alias <= addr < alias + self.size:
                return True
        return False

    def is_writable(self):
        return 'W' in self.access

    def __repr__(self):
        return f'MemoryRegion({self.name}, 0x{self.base:08X}, 0x{self.size:X}, {self.access})'


# Default NanoSoC memory map (from generated nanosoc_memmap.h)
DEFAULT_REGIONS = [
    MemoryRegion('BOOTROM_0',       0x00000000, 0x2000,  'RX',  aliases=[0x08000000]),
    MemoryRegion('IMEM_0',          0x10000000, 0x10000, 'RWX', remap_to=0x00000000),
    MemoryRegion('DMEM_0',          0x18000000, 0x10000, 'RWX'),
    MemoryRegion('SOC_PERIPHERAL',  0x40000000, 0x10000, 'RW'),
    MemoryRegion('DMAC_CTRL',       0x50000000, 0x10000, 'RW'),
    MemoryRegion('EXP',             0x60000000, 0x20000000, 'RW'),
    MemoryRegion('SRAM_0',          0x80000000, 0x10000, 'RWX'),
    MemoryRegion('SRAM_1',          0x90000000, 0x10000, 'RWX'),
    MemoryRegion('PPB',             0xE0000000, 0x100000, 'RW'),  # Private Peripheral Bus
    MemoryRegion('SYSTABLE',        0xF0000000, 0x10000, 'RO'),
]

# Default system clock frequency
DEFAULT_CLK_FREQ_HZ = 100_000_000

# UART2 register addresses (CMSDK APB UART)
UART2_BASE = 0x40006000
UART2_BAUDDIV_OFFSET = 0x10

# Cortex-M0 vector table offsets
VECTOR_TABLE = {
    0x00: 'Initial_SP',
    0x04: 'Reset_Handler',
    0x08: 'NMI_Handler',
    0x0C: 'HardFault_Handler',
    0x2C: 'SVC_Handler',
    0x38: 'PendSV_Handler',
    0x3C: 'SysTick_Handler',
}

# Branch-to-self opcode (Thumb: B . = 0xE7FE)
BRANCH_TO_SELF_OPCODE = 0xe7fe


class MemoryMap:
    """Memory map for address resolution."""

    def __init__(self, regions=None, clk_freq_hz=None):
        self.regions = regions or list(DEFAULT_REGIONS)
        self.clk_freq_hz = clk_freq_hz or DEFAULT_CLK_FREQ_HZ

    def lookup(self, addr):
        """Find the memory region containing an address."""
        for r in self.regions:
            if r.contains(addr):
                return r
        return None

    def describe_addr(self, addr):
        """Return a human-readable description of an address."""
        region = self.lookup(addr)
        if region:
            offset = addr - region.base
            return f'{region.name} + 0x{offset:X}'
        return f'UNMAPPED (0x{addr:08X})'

    @classmethod
    def from_header(cls, header_path):
        """Parse a nanosoc_memmap.h file to build the memory map."""
        path = Path(header_path)
        if not path.exists():
            return cls()

        text = path.read_text()
        regions = []
        bases = {}
        sizes = {}
        clk_freq = DEFAULT_CLK_FREQ_HZ

        for line in text.splitlines():
            m = re.match(r'#define\s+NANOSOC_(\w+)_BASE\s+\(0x([0-9A-Fa-f]+)UL\)', line)
            if m:
                bases[m.group(1)] = int(m.group(2), 16)
            m = re.match(r'#define\s+NANOSOC_(\w+)_SIZE\s+\(0x([0-9A-Fa-f]+)UL\)', line)
            if m:
                sizes[m.group(1)] = int(m.group(2), 16)
            m = re.match(r'#define\s+NANOSOC_SYS_CLK_FREQ_HZ\s+\((\d+)UL\)', line)
            if m:
                clk_freq = int(m.group(1))

        for name, base in bases.items():
            size = sizes.get(name, 0)
            if size > 0:
                regions.append(MemoryRegion(name, base, size))

        return cls(regions or None, clk_freq)
