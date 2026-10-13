"""Lightweight streaming VCD parser with signal filtering.

Designed for large VCD files (100MB+). Parses header to build signal map,
then streams value changes for filtered signals only.
"""

import re
from pathlib import Path
from typing import Dict, Generator, List, Optional, Set, Tuple


class VcdSignal:
    """A signal declared in the VCD header."""
    __slots__ = ('id', 'name', 'width', 'scope')

    def __init__(self, sig_id, name, width, scope):
        self.id = sig_id
        self.name = name
        self.width = width
        self.scope = scope

    @property
    def full_name(self):
        return f'{self.scope}.{self.name}' if self.scope else self.name


class VcdChange:
    """A value change event."""
    __slots__ = ('timestamp', 'signal_name', 'value', 'width')

    def __init__(self, timestamp, signal_name, value, width):
        self.timestamp = timestamp
        self.signal_name = signal_name
        self.value = value
        self.width = width


def _match_filter(full_name, patterns):
    """Check if a signal name matches any filter pattern (supports * glob)."""
    name_lower = full_name.lower()
    for pat in patterns:
        pat_lower = pat.lower()
        if '*' in pat_lower:
            # Simple glob: *foo* matches anything containing foo
            parts = pat_lower.split('*')
            pos = 0
            match = True
            for part in parts:
                if not part:
                    continue
                idx = name_lower.find(part, pos)
                if idx < 0:
                    match = False
                    break
                pos = idx + len(part)
            if match:
                return True
        else:
            if pat_lower in name_lower:
                return True
    return False


class VcdParser:
    """Streaming VCD file parser."""

    def __init__(self, filepath, signal_filters=None):
        """
        Args:
            filepath: Path to VCD file
            signal_filters: List of signal name patterns to track (e.g., ['*HADDR*', '*lockup*'])
                           If None, tracks all signals (warning: memory intensive)
        """
        self.filepath = Path(filepath)
        self.filters = signal_filters
        self.signals = {}        # type: Dict[str, VcdSignal]  id -> signal
        self.tracked_ids = set() # type: Set[str]  signal IDs matching filter

    def parse_header(self):
        """Parse VCD header to discover signals. Must be called before streaming."""
        if not self.filepath.exists():
            return

        scope_stack = []

        with open(str(self.filepath), 'r', errors='replace') as f:
            for line in f:
                line = line.strip()

                if line.startswith('$scope'):
                    parts = line.split()
                    if len(parts) >= 3:
                        scope_stack.append(parts[2])

                elif line.startswith('$upscope'):
                    if scope_stack:
                        scope_stack.pop()

                elif line.startswith('$var'):
                    parts = line.split()
                    if len(parts) >= 5:
                        width = int(parts[2])
                        sig_id = parts[3]
                        name = parts[4].rstrip('$end').strip()
                        scope = '.'.join(scope_stack)
                        sig = VcdSignal(sig_id, name, width, scope)
                        self.signals[sig_id] = sig

                        # Check filter
                        if self.filters is None or _match_filter(sig.full_name, self.filters):
                            self.tracked_ids.add(sig_id)

                elif line.startswith('$enddefinitions'):
                    break

    def stream_changes(self, start_time=None, end_time=None):
        """Generator yielding VcdChange events for tracked signals.

        Args:
            start_time: Skip events before this timestamp (ns)
            end_time: Stop after this timestamp (ns)
        """
        if not self.filepath.exists() or not self.tracked_ids:
            return

        current_time = 0
        in_header = True

        with open(str(self.filepath), 'r', errors='replace') as f:
            for line in f:
                line = line.strip()

                if in_header:
                    if line.startswith('$enddefinitions'):
                        in_header = False
                    continue

                if not line:
                    continue

                # Timestamp
                if line.startswith('#'):
                    try:
                        current_time = int(line[1:])
                    except ValueError:
                        continue
                    if end_time is not None and current_time > end_time:
                        return
                    continue

                if start_time is not None and current_time < start_time:
                    continue

                # Value change: binary (0/1/x/z followed by ID) or vector (b... ID)
                if line[0] in '01xzXZ':
                    val = line[0]
                    sig_id = line[1:]
                    if sig_id in self.tracked_ids:
                        sig = self.signals[sig_id]
                        int_val = 1 if val == '1' else 0
                        yield VcdChange(current_time, sig.full_name, int_val, sig.width)

                elif line.startswith('b') or line.startswith('B'):
                    parts = line.split()
                    if len(parts) >= 2:
                        sig_id = parts[1]
                        if sig_id in self.tracked_ids:
                            sig = self.signals[sig_id]
                            bits = parts[0][1:]  # strip 'b' prefix
                            try:
                                int_val = int(bits.replace('x', '0').replace('z', '0'), 2)
                            except ValueError:
                                int_val = 0
                            yield VcdChange(current_time, sig.full_name, int_val, sig.width)

    def get_signal_names(self):
        """Return list of tracked signal names."""
        return [self.signals[sid].full_name for sid in self.tracked_ids]

    def extract_around_time(self, target_time, window_ns=100):
        """Extract signal values in a time window around a target time."""
        events = []
        for change in self.stream_changes(
            start_time=target_time - window_ns,
            end_time=target_time + window_ns,
        ):
            events.append(change)
        return events
