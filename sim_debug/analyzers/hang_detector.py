"""Detect simulation hangs: branch-to-self loops, tight loops, no progress."""

from ..config import BRANCH_TO_SELF_OPCODE
from .fault_detector import Finding


class HangDetector:
    """Detect simulation hang patterns from TARMAC state."""

    def __init__(self, symbols=None):
        self.symbols = symbols
        self.findings = []

    def analyse(self, state, adp_result=None):
        """Check for hang patterns."""
        self._check_branch_to_self(state, adp_result)
        self._check_tight_loop(state)
        return self.findings

    def _check_branch_to_self(self, state, adp_result):
        """Detect branch-to-self that isn't the expected end-of-test."""
        recent = state.get_context(20)
        last_instrs = [e for e in recent if e.event_type == 'IT']

        if len(last_instrs) < 3:
            return

        # Check if last 3+ instructions are at the same PC
        tail_pcs = [e.pc for e in last_instrs[-5:]]
        if len(set(tail_pcs)) != 1:
            return

        pc = tail_pcs[0]
        opcode = last_instrs[-1].opcode

        # Check if test passed — if so, this is expected
        if adp_result and adp_result.test_result == 'PASSED':
            return  # Expected end-of-test spin

        if adp_result and adp_result.test_ended:
            return  # Test ended normally

        sym = self.symbols.describe(pc) if self.symbols else f'0x{pc:08X}'

        if opcode and (opcode & 0xFFFF) == BRANCH_TO_SELF_OPCODE:
            self.findings.append(Finding(
                Finding.ERROR,
                f'CPU stuck in branch-to-self at {sym}',
                [
                    f'PC: 0x{pc:08X}',
                    f'This may be a fault handler (HardFault, NMI) or an unexpected spin loop',
                    f'Time: {last_instrs[-1].timestamp_ns} ns',
                ],
            ))
        else:
            self.findings.append(Finding(
                Finding.WARN,
                f'CPU stuck in tight loop at {sym}',
                [f'PC: 0x{pc:08X}', f'Time: {last_instrs[-1].timestamp_ns} ns'],
            ))

    def _check_tight_loop(self, state):
        """Detect tight loop (small PC range over many instructions)."""
        recent = state.get_context(100)
        instrs = [e for e in recent if e.event_type == 'IT']

        if len(instrs) < 50:
            return

        pcs = [e.pc for e in instrs[-50:]]
        pc_range = max(pcs) - min(pcs) if pcs else 0

        # If 50 instructions fit in 32 bytes, it's a very tight loop
        if pc_range <= 32 and len(set(pcs)) <= 8:
            # Don't duplicate branch-to-self findings
            if len(set(pcs)) > 1:
                self.findings.append(Finding(
                    Finding.WARN,
                    f'Tight polling loop detected (PC range: {pc_range} bytes)',
                    [
                        f'PC range: 0x{min(pcs):08X} - 0x{max(pcs):08X}',
                        f'Unique PCs: {len(set(pcs))}',
                        f'This may be a busy-wait loop (e.g., UART TX ready polling)',
                    ],
                ))
