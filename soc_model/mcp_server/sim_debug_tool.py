# Copyright 2026, SoC Labs (www.soclabs.org)
"""MCP tool ``sim_debug`` (plan name ``sim.debug``): post-simulation analysis as JSON.

Register on a server with ``register(srv)``.  Failures are returned as
``{"ok": False, "error": {...}}`` like the other tools.
"""

from pathlib import Path
from typing import Any, Dict, Optional


def sim_debug(sim_dir: str, quick: bool = True, context: int = 20,
              tarmac: Optional[str] = None, log: Optional[str] = None,
              elf: Optional[str] = None, memmap: Optional[str] = None,
              vcd: Optional[str] = None,
              vcd_signals: Optional[str] = None) -> Dict[str, Any]:
    """sim.debug: analyse a simulation directory (TARMAC, ADP log, ELF, VCD); verdict PASS or FAIL plus findings."""
    from sim_debug import analyze
    d = Path(sim_dir)
    if not d.is_dir():
        return {'ok': False, 'error': {'code': 'no_sim_dir',
                                       'message': f'not a directory: {sim_dir}'}}
    try:
        report = analyze(str(d), tarmac=tarmac, log=log, vcd=vcd, elf=elf,
                         memmap_path=memmap, context=context, quick=quick,
                         vcd_signals=vcd_signals)
    except Exception as e:  # parsers raise on malformed input
        return {'ok': False, 'error': {'code': 'analysis_failed',
                                       'message': f'{type(e).__name__}: {e}'}}
    out = {'ok': True}
    out.update(report.to_dict())
    return out


def register(srv) -> None:
    """Add the ``sim_debug`` tool to an MCPServer."""
    srv.tool()(sim_debug)
