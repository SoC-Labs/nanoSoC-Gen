"""Shared hierarchy-walk + formatting helpers for backends (doc 02 M5).

The start of the `WalkingBackend` mixin the doc proposes: a single home for the
size formatter that was copy-pasted across backends, plus (future) the
hierarchy-walk + RegisterMap→YAML helpers.

NOTE on scope (verified, not assumed): the doc described the size formatter as
"3 verified copies". It is actually **2** identical copies (`docs.py`, `text.py`)
plus a **third, divergent** one in `sram.py` that deliberately omits the GB
branch — `sram.py` only formats SRAM-macro sizes (always < 1 GB), whereas
`docs.py`/`text.py` format address windows that include multi-GB passthrough
apertures (e.g. the 3.5 GB CPU passthrough) and so need GB. Consolidating
`sram.py` onto this GB-aware version would change its formatter's contract
(and would not be byte-identical if it ever saw a >=1 GB region), so `sram.py`
keeps its own variant for now; reconciling the two is a behaviour decision for a
later, reviewed milestone, not this byte-identical refactor.
"""


def fmt_size(size: int) -> str:
    """Format a byte size as human-readable (B/KB/MB/GB).

    Byte-identical to the formatter previously duplicated in docs.py and
    text.py. Whole multiples render without a decimal; otherwise one decimal.
    """
    if size >= 1024 * 1024 * 1024:
        v = size / (1024 * 1024 * 1024)
        return f"{v:.0f} GB" if v == int(v) else f"{v:.1f} GB"
    if size >= 1024 * 1024:
        v = size / (1024 * 1024)
        return f"{v:.0f} MB" if v == int(v) else f"{v:.1f} MB"
    if size >= 1024:
        v = size / 1024
        return f"{v:.0f} KB" if v == int(v) else f"{v:.1f} KB"
    return f"{size} B"


class WalkingBackend:
    """Mixin for backends that walk the instance hierarchy + format sizes.

    Currently provides the shared size formatter; the hierarchy-walk +
    RegisterMap→YAML consolidation (doc 02 M5) lands here next, once each
    backend's walk is verified equivalent.
    """

    fmt_size = staticmethod(fmt_size)
