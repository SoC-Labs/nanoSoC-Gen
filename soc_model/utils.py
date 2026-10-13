"""Utility functions for parameter resolution and bit-slice parsing."""

import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def resolve_param_ref(value: Any, params: Dict[str, Any]) -> Any:
    """Resolve $PARAM references in a value.

    Handles strings like "$SYS_ADDR_W" -> 32, and expressions like
    "4 * (2 ** $SRAM_0_RAM_ADDR_W)" -> evaluated result.
    """
    if not isinstance(value, str):
        return value

    # Check for pure parameter reference: "$PARAM_NAME"
    m = re.fullmatch(r'\$(\w+)', value)
    if m:
        param_name = m.group(1)
        if param_name in params:
            return params[param_name]
        return value  # unresolved

    # Check for expression containing $PARAM references
    if '$' in value:
        resolved = value
        for param_name, param_val in params.items():
            resolved = resolved.replace(f'${param_name}', str(param_val))
        # Try to evaluate as arithmetic expression (safe subset)
        if '$' not in resolved:
            try:
                return _safe_eval(resolved)
            except Exception:
                return resolved
        return resolved

    return value


def _safe_eval(expr: str) -> Any:
    """Evaluate simple arithmetic expressions safely."""
    # Only allow digits, operators, parens, whitespace
    if re.fullmatch(r'[\d\s\+\-\*\/\(\)\.\*\*]+', expr):
        return eval(expr, {"__builtins__": {}})
    return expr


def resolve_params_in_dict(d: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively resolve $PARAM references in a dictionary."""
    result = {}
    for k, v in d.items():
        if isinstance(v, dict):
            result[k] = resolve_params_in_dict(v, params)
        elif isinstance(v, list):
            result[k] = [resolve_param_ref(item, params) if not isinstance(item, (dict, list)) else item for item in v]
        else:
            result[k] = resolve_param_ref(v, params)
    return result


def parse_bit_slice(signal: str) -> Tuple[str, Optional[int], Optional[int]]:
    """Parse a signal name with optional bit slice.

    Returns (base_name, high_bit, low_bit).
    Examples:
        "cpu_0_irq[31:16]" -> ("cpu_0_irq", 31, 16)
        "p1_in[7]"         -> ("p1_in", 7, 7)
        "sys_hclk"         -> ("sys_hclk", None, None)
        "u_ss_cpu.sys_hclk" -> ("u_ss_cpu.sys_hclk", None, None)
        "u_ss_systemctrl.P1_OUT_MUX[6:4]" -> ("u_ss_systemctrl.P1_OUT_MUX", 6, 4)
    """
    m = re.match(r'^(.+?)\[(\d+):(\d+)\]$', signal)
    if m:
        return m.group(1), int(m.group(2)), int(m.group(3))

    m = re.match(r'^(.+?)\[(\d+)\]$', signal)
    if m:
        return m.group(1), int(m.group(2)), int(m.group(2))

    return signal, None, None


def bit_slice_width(high: Optional[int], low: Optional[int]) -> Optional[int]:
    """Calculate width of a bit slice. Returns None if no slice."""
    if high is None or low is None:
        return None
    return high - low + 1


def parse_conn_ref(conn: str) -> Tuple[Optional[str], str, Optional[int], Optional[int]]:
    """Parse a connection reference.

    Returns (instance_name, port_name, high_bit, low_bit).
    Examples:
        "u_ss_cpu.sys_hclk"     -> ("u_ss_cpu", "sys_hclk", None, None)
        "nanosoc_ahb_interconnect.cpu_0" -> ("nanosoc_ahb_interconnect", "cpu_0", None, None)
        "sys_sysresetn"         -> (None, "sys_sysresetn", None, None)
        "p1_in[6:0]"           -> (None, "p1_in", 6, 0)
        "u_ss_systemctrl.P1_OUT_MUX[6:4]" -> ("u_ss_systemctrl", "P1_OUT_MUX", 6, 4)
    """
    base, high, low = parse_bit_slice(conn)

    # Split on first dot to get instance.port
    parts = base.split('.', 1)
    if len(parts) == 2:
        return parts[0], parts[1], high, low
    return None, parts[0], high, low


def get_param_value(params: Dict[str, Any], name: str) -> Any:
    """Get a parameter value from a params dict, handling nested format.

    Params can be either {name: value} or {name: {type:, default:, desc:}}.
    """
    val = params.get(name)
    if isinstance(val, dict) and 'default' in val:
        return val['default']
    return val


def flatten_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten a params dict from {name: {type, default, desc}} to {name: value}."""
    result = {}
    for name, val in params.items():
        if isinstance(val, dict) and 'default' in val:
            result[name] = val['default']
        else:
            result[name] = val
    return result


# ---------------------------------------------------------------------------
# Write-if-changed utility
# ---------------------------------------------------------------------------

_TIMESTAMP_LINE_RE = re.compile(
    r'^\s*('
    r'//\s*Generated:\s*\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}'
    r'|\*\s*Generated:\s*\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}'
    r'|#\s*Generated:\s*\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}'
    r'|>\s*Auto-generated.*on\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}.*'
    r'|//\s*Copyright\s+\d{4},.*'
    r')\s*$'
)


def _strip_timestamps(content: str) -> str:
    """Remove lines that contain only generated timestamps."""
    return '\n'.join(
        line for line in content.splitlines()
        if not _TIMESTAMP_LINE_RE.match(line)
    )


def _strip_leading_timescale(content: str) -> str:
    """Drop a single leading ```timescale`` directive for change-detection.

    The end-of-run sweep in ``__main__`` prepends ```timescale 1ns/1ps`` to
    generated Verilog/SystemVerilog (so downstream tools that require an explicit
    time unit compile cleanly). write_if_changed itself writes the bare body, so
    on the next regen the on-disk file carries the swept-in directive while the
    fresh content does not. Normalising it out here keeps unchanged files from
    being needlessly rewritten (no churn) without write_if_changed having to bake
    in the timescale convention.
    """
    lines = content.splitlines()
    if lines and lines[0].lstrip().startswith('`timescale'):
        return '\n'.join(lines[1:])
    return content


def write_if_changed(path, content: str) -> bool:
    """Write *content* to *path* only if meaningful (non-timestamp) content differs.

    Returns True if the file was written, False if skipped.
    """
    path = Path(path)
    if path.exists():
        existing = path.read_text()
        if (_strip_timestamps(_strip_leading_timescale(existing))
                == _strip_timestamps(_strip_leading_timescale(content))):
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return True


# A linker-profile region's `origin:` (LinkerRegion.origin) must be word
# aligned: 4 bytes. Every CPU these profiles link for is ARMv6-M or ARMv7-M,
# and ARMv6-M (Cortex-M0/M0+) has no unaligned access at all, so a region
# whose ORIGIN is not a multiple of 4 puts the first word of .data (or of
# whatever the linker places there) where a word load or store faults.
# 8 bytes is not required here: the initial stack pointer is ORIGIN + LENGTH
# (sections*.ld __StackTop), so AAPCS stack alignment depends on the LENGTH
# as well, and a region need not hold the stack at all.
LINKER_ORIGIN_ALIGN = 4


def linker_origin(value: Any, where: str) -> int:
    """Validate a linker region's ``origin:`` and return it as an int.

    Accepted: an int, or a string holding one integer literal (``"0x2000_0000"``).
    Refused (ValueError naming ``where``): bool (YAML ``true`` is 1), float,
    anything else that is not an integer, a value outside 0 .. 0xFFFF_FFFF,
    and one that is not word aligned (LINKER_ORIGIN_ALIGN).
    """
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"{where}: origin must be an integer address, "
                         f"not {type(value).__name__} {value!r}")
    if isinstance(value, str):
        try:
            v = int(value.strip(), 0)
        except ValueError:
            raise ValueError(f"{where}: origin {value!r} is not an integer") from None
    else:
        v = value
    if not 0 <= v <= 0xFFFFFFFF:
        raise ValueError(f"{where}: origin {value!r} is outside the 32-bit address "
                         f"space (0x00000000 .. 0xFFFFFFFF)")
    if v % LINKER_ORIGIN_ALIGN:
        raise ValueError(f"{where}: origin 0x{v:08X} is not word aligned "
                         f"(a multiple of {LINKER_ORIGIN_ALIGN})")
    return v
