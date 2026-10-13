"""Check for invalid memory accesses: ROM writes, unmapped access, SP validation."""

from ..config import MemoryMap
from .fault_detector import Finding


class MemoryChecker:
    """Analyse TARMAC memory events for access violations."""

    def __init__(self, memmap=None, symbols=None):
        self.memmap = memmap or MemoryMap()
        self.symbols = symbols
        self.findings = []
        self._rom_writes = []
        self._unmapped = []

    def check_event(self, evt):
        """Check a single memory event."""
        if evt.event_type == 'MW' and evt.mem_addr is not None:
            region = self.memmap.lookup(evt.mem_addr)
            if region and not region.is_writable():
                self._rom_writes.append(evt)
            elif region is None:
                self._unmapped.append(evt)

        if evt.event_type == 'MR' and evt.mem_addr is not None:
            region = self.memmap.lookup(evt.mem_addr)
            if region is None and evt.mem_kind == 'D':
                self._unmapped.append(evt)

    def finalise(self, state):
        """Produce findings from accumulated events."""
        if self._rom_writes:
            first = self._rom_writes[0]
            region = self.memmap.lookup(first.mem_addr)
            region_name = region.name if region else 'UNKNOWN'
            self.findings.append(Finding(
                Finding.ERROR,
                f'Write to read-only memory ({len(self._rom_writes)} occurrences)',
                [
                    f'First write: 0x{first.mem_addr:08X} ({region_name}) at {first.timestamp_ns} ns',
                    f'Data: 0x{first.mem_data:08X}' if first.mem_data is not None else 'Data: unknown',
                    f'Total ROM writes: {len(self._rom_writes)}',
                ],
            ))

        if self._unmapped:
            first = self._unmapped[0]
            self.findings.append(Finding(
                Finding.WARN,
                f'Access to unmapped address ({len(self._unmapped)} occurrences)',
                [
                    f'First access: 0x{first.mem_addr:08X} at {first.timestamp_ns} ns',
                    f'Type: {"write" if first.mem_write else "read"}',
                ],
            ))

        return self.findings


class VectorTableChecker:
    """Validate the firmware vector table."""

    def __init__(self, memmap=None, symbols=None):
        self.memmap = memmap or MemoryMap()
        self.symbols = symbols
        self.findings = []

    def check(self, symbol_table):
        """Validate vector table from ELF symbol table."""
        if symbol_table is None:
            return self.findings

        # Check initial SP
        sp = symbol_table.initial_sp
        if sp is not None:
            region = self.memmap.lookup(sp - 1)  # SP points to top, check sp-1
            if region and region.is_writable():
                self.findings.append(Finding(
                    Finding.INFO,
                    f'Initial SP: 0x{sp:08X} ({self.memmap.describe_addr(sp)})',
                ))
            elif region:
                self.findings.append(Finding(
                    Finding.ERROR,
                    f'Initial SP in non-writable region: 0x{sp:08X} ({region.name})',
                    ['SP must point to top of a writable RAM region (DMEM_0, SRAM_0, etc.)'],
                ))
            else:
                self.findings.append(Finding(
                    Finding.ERROR,
                    f'Initial SP in unmapped region: 0x{sp:08X}',
                    ['SP must point to top of a writable RAM region'],
                ))

        # Check reset handler
        rh = symbol_table.reset_handler
        if rh is not None:
            if rh & 1 == 0:
                self.findings.append(Finding(
                    Finding.ERROR,
                    f'Reset handler not Thumb: 0x{rh:08X} (bit 0 must be set)',
                ))
            else:
                sym = self.symbols.describe(rh & ~1) if self.symbols else f'0x{rh & ~1:08X}'
                self.findings.append(Finding(
                    Finding.INFO,
                    f'Reset handler: 0x{rh:08X} ({sym})',
                ))

        # Check sections
        sections = symbol_table.get_sections()
        if '.data' in sections and '.text' in sections:
            data_vma, data_lma, data_size = sections['.data']
            text_vma, text_lma, text_size = sections['.text']

            data_region = self.memmap.lookup(data_vma)
            if data_region and not data_region.is_writable():
                self.findings.append(Finding(
                    Finding.ERROR,
                    f'.data section VMA in non-writable region: 0x{data_vma:08X} ({data_region.name})',
                    ['The .data section must be in writable RAM for the startup code to copy initialised data'],
                ))

        return self.findings
