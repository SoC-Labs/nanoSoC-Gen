"""Streaming TARMAC trace parser for Cortex-M0 execution traces.

TARMAC format (observed from VCS simulation):
    <timestamp> ns <type> [fields...]

Types:
    IT <addr> <opcode> <disasm>    — Instruction executed (Thumb)
    IS <addr> <opcode> <disasm>    — Instruction skipped (conditional)
    MR4_D <addr> <data>            — Memory read 4-byte data
    MR4_I <addr> <data>            — Memory read 4-byte instruction fetch
    MW4_D <addr> <data>            — Memory write 4-byte data
    MW4ED <addr> <data>            — Memory write 4-byte with exception
    R <reg> <value> [annotation]   — Register update
    E <event_name> [args]          — Exception/event
"""

from collections import deque
from pathlib import Path
from typing import Optional


class TarmacEvent:
    """A parsed TARMAC trace event."""

    __slots__ = (
        'timestamp_ns', 'event_type', 'pc', 'opcode', 'disasm',
        'mem_addr', 'mem_data', 'mem_kind', 'mem_write', 'mem_exception',
        'reg_name', 'reg_value', 'exception_name', 'exception_args', 'raw',
    )

    def __init__(self):
        self.timestamp_ns = 0
        self.event_type = ''     # 'IT', 'IS', 'MR', 'MW', 'R', 'E'
        self.pc = None           # int: instruction address
        self.opcode = None       # int: instruction opcode
        self.disasm = None       # str: disassembly text
        self.mem_addr = None     # int: memory address
        self.mem_data = None     # int or None: memory data
        self.mem_kind = None     # 'D' (data) or 'I' (instruction fetch)
        self.mem_write = False   # True for MW, False for MR
        self.mem_exception = False  # True for MW4ED
        self.reg_name = None     # str: register name
        self.reg_value = None    # int: register value
        self.exception_name = None  # str: exception event name
        self.exception_args = None  # str: extra args
        self.raw = ''            # original line


def parse_tarmac_line(line):
    """Parse a single TARMAC line into a TarmacEvent. Returns None for unparseable lines."""
    line = line.rstrip()
    if not line:
        return None

    parts = line.split()
    if len(parts) < 3:
        return None

    # Parse timestamp
    try:
        ts = int(parts[0])
    except ValueError:
        return None

    if parts[1] != 'ns':
        return None

    evt = TarmacEvent()
    evt.timestamp_ns = ts
    evt.raw = line
    tag = parts[2]

    if tag in ('IT', 'IS'):
        evt.event_type = tag
        if len(parts) >= 5:
            try:
                evt.pc = int(parts[3], 16)
                evt.opcode = int(parts[4], 16)
            except ValueError:
                return None
            evt.disasm = ' '.join(parts[5:]) if len(parts) > 5 else ''
        return evt

    if tag.startswith('MR') or tag.startswith('MW'):
        evt.mem_write = tag.startswith('MW')
        evt.mem_exception = 'ED' in tag
        # Extract kind: MR4_D -> D, MR4_I -> I
        if '_' in tag:
            evt.mem_kind = tag.split('_', 1)[1]
        evt.event_type = 'MW' if evt.mem_write else 'MR'
        if len(parts) >= 5:
            try:
                evt.mem_addr = int(parts[3], 16)
                evt.mem_data = int(parts[4], 16) if parts[4] != 'xxxxxxxx' else None
            except ValueError:
                return None
        return evt

    if tag == 'R':
        evt.event_type = 'R'
        if len(parts) >= 5:
            evt.reg_name = parts[3]
            try:
                evt.reg_value = int(parts[4], 16)
            except ValueError:
                evt.reg_value = None
        return evt

    if tag == 'E':
        evt.event_type = 'E'
        evt.exception_name = parts[3] if len(parts) > 3 else ''
        evt.exception_args = ' '.join(parts[4:]) if len(parts) > 4 else ''
        return evt

    return None


def parse_tarmac(filepath, max_events=None):
    """Generator that streams TarmacEvent objects from a TARMAC log file."""
    count = 0
    with open(str(filepath), 'r', errors='replace') as f:
        for line in f:
            evt = parse_tarmac_line(line)
            if evt is not None:
                yield evt
                count += 1
                if max_events and count >= max_events:
                    return


class TarmacAnalysisState:
    """Maintains rolling state while processing TARMAC events."""

    def __init__(self, context_size=30):
        self.recent = deque(maxlen=context_size)
        self.registers = {}   # reg_name -> value
        self.last_pc = None
        self.instruction_count = 0
        self.last_timestamp = 0
        self.unique_pcs = set()

    def update(self, evt):
        """Process an event and update state."""
        self.recent.append(evt)
        self.last_timestamp = evt.timestamp_ns

        if evt.event_type in ('IT', 'IS'):
            self.last_pc = evt.pc
            self.instruction_count += 1
            self.unique_pcs.add(evt.pc)

        if evt.event_type == 'R':
            self.registers[evt.reg_name] = evt.reg_value

    def get_context(self, n=20):
        """Return the last N events as a list."""
        items = list(self.recent)
        return items[-n:] if len(items) > n else items

    def get_register_snapshot(self):
        """Return current register values."""
        return dict(self.registers)
