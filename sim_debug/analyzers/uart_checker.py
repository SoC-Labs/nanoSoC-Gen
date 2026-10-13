"""UART/ADP output analysis: baud rate mismatch, garbled output detection."""

from ..config import MemoryMap, UART2_BASE, UART2_BAUDDIV_OFFSET
from .fault_detector import Finding


class UartChecker:
    """Analyse ADP output and TARMAC for UART issues."""

    def __init__(self, memmap=None):
        self.memmap = memmap or MemoryMap()
        self.findings = []
        self._bauddiv_writes = []

    def check_event(self, evt):
        """Check TARMAC events for BAUDDIV register writes."""
        if evt.event_type == 'MW' and evt.mem_addr is not None:
            if evt.mem_addr == UART2_BASE + UART2_BAUDDIV_OFFSET:
                self._bauddiv_writes.append(evt)

    def analyse(self, adp_result):
        """Analyse ADP output for issues."""
        if adp_result is None:
            return self.findings

        # Check for garbled output
        if adp_result.garbled:
            self.findings.append(Finding(
                Finding.ERROR,
                f'Garbled ADP output detected (printable ratio: {adp_result.garbled_ratio:.0%})',
                [
                    'Firmware output contains non-printable characters',
                    'Common causes: baud rate mismatch, wrong clock frequency, GPIO mux conflict',
                ] + [f'  Line: {l!r}' for l in adp_result.firmware_lines[:5]],
            ))

        # Check baud rate from TARMAC
        if self._bauddiv_writes:
            for evt in self._bauddiv_writes:
                if evt.mem_data is not None and evt.mem_data > 0:
                    baud = self.memmap.clk_freq_hz // evt.mem_data
                    self.findings.append(Finding(
                        Finding.INFO,
                        f'UART2 BAUDDIV={evt.mem_data} (baud rate ~{baud} at {self.memmap.clk_freq_hz/1e6:.0f} MHz)',
                        [f'Written at {evt.timestamp_ns} ns'],
                    ))

        # Check simulation outcome
        if adp_result.test_result == 'PASSED':
            self.findings.append(Finding(
                Finding.INFO, '** TEST PASSED **',
            ))
        elif adp_result.test_result == 'FAILED':
            self.findings.append(Finding(
                Finding.ERROR, '** TEST FAILED **',
                [f'Firmware lines: {len(adp_result.firmware_lines)}'],
            ))
        elif not adp_result.test_ended and adp_result.banner:
            self.findings.append(Finding(
                Finding.WARN, 'Test did not complete (no Test Ended marker)',
                ['Simulation may have timed out or hung'],
            ))

        if not adp_result.banner:
            self.findings.append(Finding(
                Finding.WARN, 'No boot banner detected',
                ['Expected: SoCLabs NanoSoC... banner from bootloader'],
            ))

        if adp_result.banner and not adp_result.remap:
            self.findings.append(Finding(
                Finding.ERROR, 'Boot banner seen but no REMAP',
                ['Bootloader started but did not remap IMEM — firmware may not have been loaded'],
            ))

        return self.findings
