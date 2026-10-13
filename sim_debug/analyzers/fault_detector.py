"""Detect CPU faults: HardFault, NMI, data abort, lockup."""

from ..config import MemoryMap, BRANCH_TO_SELF_OPCODE


class Finding:
    """A diagnostic finding with severity and detail."""

    FATAL = 'FATAL'
    ERROR = 'ERROR'
    WARN = 'WARN'
    INFO = 'INFO'

    def __init__(self, severity, title, details=None, context=None, data=None):
        self.severity = severity
        self.title = title
        self.details = details or []    # list of detail lines
        self.context = context or []    # list of TarmacEvent for instruction context
        self.data = data or {}          # structured fields (pc, cause, time_ns ...) for JSON


class FaultDetector:
    """Analyse TARMAC events for CPU faults."""

    FAULT_EVENTS = {
        'HARDFAULT_REQ', 'DATA_OR_ALIGNMENT_ABORT',
        'NMI_REQ', 'BUSFAULT_REQ', 'USAGEFAULT_REQ',
        'LOCKUP',
    }

    def __init__(self, memmap=None, symbols=None, context_size=20):
        self.memmap = memmap or MemoryMap()
        self.symbols = symbols
        self.context_size = context_size
        self.findings = []

    def analyse(self, state):
        """Analyse a TarmacAnalysisState for fault patterns.

        Args:
            state: TarmacAnalysisState with all events processed
        """
        self._check_events(state)
        self._check_final_state(state)
        return self.findings

    def check_event(self, evt, state):
        """Check a single event as it arrives (online analysis)."""
        if evt.event_type == 'E' and evt.exception_name in self.FAULT_EVENTS:
            self._record_fault(evt, state)

    def _check_events(self, state):
        """Scan recent events for fault patterns."""
        for evt in state.recent:
            if evt.event_type == 'E' and evt.exception_name in self.FAULT_EVENTS:
                if not any(f.title.startswith(evt.exception_name) for f in self.findings):
                    self._record_fault(evt, state)

    def _check_final_state(self, state):
        """Check if the simulation ended in a fault handler loop."""
        recent = state.get_context(10)
        if not recent:
            return

        # Check for branch-to-self at a fault handler address
        last_instructions = [e for e in recent if e.event_type == 'IT']
        if len(last_instructions) >= 3:
            pcs = [e.pc for e in last_instructions[-3:]]
            if len(set(pcs)) == 1:  # Same PC repeated
                pc = pcs[0]
                last = last_instructions[-1]
                if last.opcode and (last.opcode & 0xFFFF) == BRANCH_TO_SELF_OPCODE:
                    sym = self.symbols.describe(pc) if self.symbols else f'0x{pc:08X}'
                    self.findings.append(Finding(
                        Finding.WARN,
                        f'Simulation ended at branch-to-self: {sym}',
                        [f'PC: 0x{pc:08X}', f'Opcode: 0x{last.opcode:04X} (B .)'],
                    ))

    def _record_fault(self, evt, state):
        """Record a fault finding with context."""
        context = state.get_context(self.context_size)

        # Find the last instruction before the fault
        faulting_instr = None
        faulting_mem = None
        for e in reversed(context):
            if e.timestamp_ns > evt.timestamp_ns:
                continue
            if faulting_instr is None and e.event_type in ('IT', 'IS'):
                faulting_instr = e
            if faulting_mem is None and e.event_type in ('MW', 'MR'):
                faulting_mem = e
            if faulting_instr and faulting_mem:
                break

        details = [f'Time: {evt.timestamp_ns} ns']
        data = {'exception': evt.exception_name, 'time_ns': evt.timestamp_ns}

        if faulting_instr:
            sym = self.symbols.describe(faulting_instr.pc) if self.symbols else f'0x{faulting_instr.pc:08X}'
            details.append(f'Faulting PC: {sym} (0x{faulting_instr.pc:08X})')
            data['pc'] = f'0x{faulting_instr.pc:08X}'
            data['pc_symbol'] = sym
            if faulting_instr.disasm:
                details.append(f'Instruction: {faulting_instr.disasm}')

        if faulting_mem:
            region_desc = self.memmap.describe_addr(faulting_mem.mem_addr)
            direction = 'Write to' if faulting_mem.mem_write else 'Read from'
            details.append(f'{direction}: 0x{faulting_mem.mem_addr:08X} ({region_desc})')
            region = self.memmap.lookup(faulting_mem.mem_addr)
            if faulting_mem.mem_write and region and not region.is_writable():
                details.append(f'CAUSE: Write to read-only region {region.name}')
                data['cause'] = f'Write to read-only region {region.name}'
            data['mem_addr'] = f'0x{faulting_mem.mem_addr:08X}'
            data['mem_write'] = bool(faulting_mem.mem_write)

        # Register snapshot
        regs = state.get_register_snapshot()
        if regs:
            reg_line = '  '.join(f'{k}=0x{v:08X}' for k, v in sorted(regs.items())
                                 if k.startswith('r') and v is not None)
            if reg_line:
                details.append(f'Registers: {reg_line}')

        severity = Finding.FATAL if evt.exception_name == 'HARDFAULT_REQ' else Finding.ERROR
        self.findings.append(Finding(
            severity,
            f'{evt.exception_name} at {evt.timestamp_ns} ns',
            details,
            [e for e in context if e.event_type in ('IT', 'IS', 'MW', 'MR')],
            data,
        ))
