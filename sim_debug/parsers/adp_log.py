"""ADP and simulation log parser.

Extracts [ADP] tagged lines from simulation logs and analyses output patterns.
"""

import re
from pathlib import Path
from typing import List, Optional


class AdpOutput:
    """Parsed ADP output with analysis."""

    def __init__(self):
        self.lines = []          # type: List[str]
        self.banner = None       # type: Optional[str]
        self.remap = None        # type: Optional[str]
        self.test_result = None  # type: Optional[str]  'PASSED', 'FAILED', or None
        self.test_ended = False
        self.firmware_lines = [] # type: List[str]  lines between REMAP and Test Ended
        self.sim_time_ns = None  # type: Optional[int]
        self.garbled = False
        self.garbled_ratio = 0.0


def parse_adp_log(filepath):
    """Parse a simulation log file for ADP output."""
    result = AdpOutput()
    path = Path(filepath)
    if not path.exists():
        return result

    in_firmware = False

    with open(str(path), 'r', errors='replace') as f:
        for line in f:
            line = line.rstrip()

            # Extract simulation time from VCS report
            m = re.search(r'Time:\s+(\d+)\s*(?:ns|ps)', line)
            if m:
                val = int(m.group(1))
                if 'ps' in line:
                    val //= 1000
                result.sim_time_ns = val

            # Extract [ADP] lines
            m = re.match(r'\[ADP\]\s*(.*)', line)
            if not m:
                continue

            content = m.group(1)
            result.lines.append(content)

            # Detect banner
            if 'SoCLabs' in content and 'NanoSoC' in content:
                result.banner = content.strip()

            # Detect remap
            if 'REMAP' in content:
                result.remap = content.strip()
                in_firmware = True
                continue

            # Detect test result
            if '** TEST PASSED **' in content:
                result.test_result = 'PASSED'
                in_firmware = False
            elif '** TEST FAILED **' in content:
                result.test_result = 'FAILED'
                in_firmware = False

            # Detect test end
            if 'Test Ended' in content:
                result.test_ended = True
                in_firmware = False

            # Collect firmware output lines
            if in_firmware and content.strip():
                result.firmware_lines.append(content)

    # Check for garbled output
    if result.firmware_lines:
        total_chars = sum(len(l) for l in result.firmware_lines)
        if total_chars > 0:
            printable = sum(
                sum(1 for c in l if c.isprintable() or c in '\n\r\t')
                for l in result.firmware_lines
            )
            result.garbled_ratio = printable / total_chars
            result.garbled = result.garbled_ratio < 0.8

    return result


def parse_adp_from_capture(filepath):
    """Parse an ADP capture log (extadp_out.log / extio_adp_out.log)."""
    result = AdpOutput()
    path = Path(filepath)
    if not path.exists():
        return result

    with open(str(path), 'r', errors='replace') as f:
        for line in f:
            line = line.rstrip()
            m = re.match(r'\[ADP\]\s*(.*)', line)
            if m:
                result.lines.append(m.group(1))

    # Run same detection on the extracted lines
    in_firmware = False
    for content in result.lines:
        if 'SoCLabs' in content and 'NanoSoC' in content:
            result.banner = content.strip()
        if 'REMAP' in content:
            result.remap = content.strip()
            in_firmware = True
            continue
        if '** TEST PASSED **' in content:
            result.test_result = 'PASSED'
            in_firmware = False
        elif '** TEST FAILED **' in content:
            result.test_result = 'FAILED'
            in_firmware = False
        if 'Test Ended' in content:
            result.test_ended = True
            in_firmware = False
        if in_firmware and content.strip():
            result.firmware_lines.append(content)

    return result
