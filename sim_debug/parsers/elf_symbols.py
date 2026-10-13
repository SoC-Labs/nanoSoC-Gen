"""ELF symbol resolution and firmware binary analysis via arm-none-eabi toolchain."""

import bisect
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class Symbol:
    """A firmware symbol with address and size."""
    __slots__ = ('address', 'size', 'name', 'sym_type')

    def __init__(self, address, size, name, sym_type=''):
        self.address = address
        self.size = size
        self.name = name
        self.sym_type = sym_type


class SymbolTable:
    """Symbol table for address-to-function name resolution."""

    def __init__(self):
        self._symbols = []   # type: List[Symbol]
        self._addrs = []     # type: List[int]  sorted addresses for bisect
        self._sections = {}  # type: Dict[str, Tuple[int, int, int]]  name -> (vma, lma, size)
        self.initial_sp = None   # type: Optional[int]
        self.reset_handler = None  # type: Optional[int]
        self.vector_table = {}  # type: Dict[int, int]  offset -> address

    def lookup(self, addr):
        """Find the symbol containing an address. Returns (name, offset) or None."""
        if not self._addrs:
            return None
        idx = bisect.bisect_right(self._addrs, addr) - 1
        if idx < 0:
            return None
        sym = self._symbols[idx]
        offset = addr - sym.address
        # Allow match if within symbol size, or within 4K if size unknown
        max_offset = sym.size if sym.size > 0 else 0x1000
        if 0 <= offset < max_offset:
            return (sym.name, offset)
        return None

    def describe(self, addr):
        """Return 'function+0xNN' or hex address string."""
        result = self.lookup(addr)
        if result:
            name, offset = result
            if offset == 0:
                return name
            return f'{name}+0x{offset:X}'
        return f'0x{addr:08X}'

    @classmethod
    def from_elf(cls, elf_path):
        """Build a SymbolTable from an ELF file using arm-none-eabi-nm."""
        st = cls()
        path = Path(elf_path)
        if not path.exists():
            return st

        # Get symbols via nm
        try:
            result = subprocess.run(
                ['arm-none-eabi-nm', '-nS', str(path)],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                st._parse_nm_output(result.stdout)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Get sections via objdump
        try:
            result = subprocess.run(
                ['arm-none-eabi-objdump', '-h', str(path)],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                st._parse_sections(result.stdout)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Read vector table
        try:
            result = subprocess.run(
                ['arm-none-eabi-objdump', '-s', '-j', '.text',
                 '--start-address=0x0', '--stop-address=0x40', str(path)],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                st._parse_vector_table(result.stdout)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        return st

    def _parse_nm_output(self, text):
        """Parse arm-none-eabi-nm -nS output."""
        symbols = []
        for line in text.splitlines():
            parts = line.strip().split()
            if len(parts) < 3:
                continue
            try:
                addr = int(parts[0], 16)
            except ValueError:
                continue

            # Format: addr [size] type name
            if len(parts) == 4:
                try:
                    size = int(parts[1], 16)
                    sym_type = parts[2]
                    name = parts[3]
                except ValueError:
                    size = 0
                    sym_type = parts[1]
                    name = parts[2]
            else:
                size = 0
                sym_type = parts[1]
                name = parts[2]

            # Only keep text/data symbols
            if sym_type.lower() in ('t', 'd', 'b', 'r', 'w'):
                symbols.append(Symbol(addr, size, name, sym_type))

        # Sort by address and build lookup arrays
        symbols.sort(key=lambda s: s.address)
        self._symbols = symbols
        self._addrs = [s.address for s in symbols]

    def _parse_sections(self, text):
        """Parse objdump -h output for section layout."""
        for line in text.splitlines():
            parts = line.split()
            if len(parts) >= 7 and parts[0].isdigit():
                name = parts[1]
                try:
                    size = int(parts[2], 16)
                    vma = int(parts[3], 16)
                    lma = int(parts[4], 16)
                    self._sections[name] = (vma, lma, size)
                except (ValueError, IndexError):
                    pass

    def _parse_vector_table(self, text):
        """Parse objdump -s output for vector table contents."""
        for line in text.splitlines():
            line = line.strip()
            if not line or not line[0].isalnum():
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            try:
                addr = int(parts[0], 16)
            except ValueError:
                continue

            # Each word is 4 bytes, little-endian in hex
            for i, word_hex in enumerate(parts[1:5]):
                if len(word_hex) != 8:
                    continue
                try:
                    # objdump -s shows bytes in memory order
                    b = bytes.fromhex(word_hex)
                    value = int.from_bytes(b, 'little')
                    offset = addr + i * 4
                    self.vector_table[offset] = value
                except ValueError:
                    continue

        if 0x00 in self.vector_table:
            self.initial_sp = self.vector_table[0x00]
        if 0x04 in self.vector_table:
            self.reset_handler = self.vector_table[0x04]

    def get_sections(self):
        """Return section layout dict."""
        return dict(self._sections)
