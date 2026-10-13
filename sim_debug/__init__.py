"""NanoSoC Simulation Debug/Analysis Tool.

Automates post-simulation debugging by parsing TARMAC traces, ADP logs,
VCD waveforms, and firmware ELF files to diagnose common failures:
HardFaults, hangs, garbled UART, wrong memory layout, ROM writes, etc.

API: ``sim_debug.analyze(sim_dir, **overrides) -> Report``; ``Report.to_dict()``.
"""

__version__ = '0.1.0'


def analyze(*args, **kwargs):
    """Run the analysis and return a Report (see ``sim_debug.__main__.analyze``)."""
    from .__main__ import analyze as _analyze
    return _analyze(*args, **kwargs)
