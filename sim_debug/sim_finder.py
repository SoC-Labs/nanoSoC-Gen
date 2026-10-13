"""Auto-discover simulation artifacts in a directory."""

from pathlib import Path
from typing import Optional


class SimArtifacts:
    """Paths to simulation output files."""

    def __init__(self, sim_dir):
        self.sim_dir = Path(sim_dir).resolve()
        self.testname = self.sim_dir.name
        self.tarmac = None       # type: Optional[Path]
        self.tarmac_dbg = None   # type: Optional[Path]
        self.sim_log = None      # type: Optional[Path]
        self.adp_log = None      # type: Optional[Path]
        self.vcd = None          # type: Optional[Path]
        self.elf = None          # type: Optional[Path]
        self.memmap_h = None     # type: Optional[Path]

    def discover(self):
        """Auto-detect all simulation artifacts."""
        logs = self.sim_dir / 'logs'

        # TARMAC traces
        for name in ['tarmac0.log', 'tarmac1.log']:
            p = logs / name if logs.exists() else self.sim_dir / name
            if p.exists():
                if self.tarmac is None:
                    self.tarmac = p
                else:
                    self.tarmac_dbg = p

        # Simulation log
        if logs.exists():
            for p in sorted(logs.glob('run_*.log')):
                self.sim_log = p
                break

        # ADP log
        for name in ['extio_adp_out.log', 'extadp_out.log']:
            p = logs / name if logs.exists() else self.sim_dir / name
            if p.exists():
                self.adp_log = p
                break

        # VCD waveform
        vcd = self.sim_dir / 'waves.vcd'
        if vcd.exists():
            self.vcd = vcd

        # ELF binary — walk up from sim_dir to find build directory
        # simulate/sim/<test>/ -> project_root/build/firmware/<test>/compile/<test>.o
        project_root = self._find_project_root()
        if project_root:
            elf = project_root / 'build' / 'firmware' / self.testname / 'compile' / f'{self.testname}.o'
            if elf.exists():
                self.elf = elf

            # Memory map header
            for candidate in [
                project_root / 'nanosoc_m0_soc' / 'build_soc' / 'firmware' / 'nanosoc_memmap.h',
                project_root / 'build_soc' / 'firmware' / 'nanosoc_memmap.h',
            ]:
                if candidate.exists():
                    self.memmap_h = candidate
                    break

        return self

    def _find_project_root(self):
        """Walk up from sim_dir to find the project root (contains set_env.sh or Makefile)."""
        d = self.sim_dir
        for _ in range(10):
            if (d / 'set_env.sh').exists() or (d / 'Makefile').exists():
                return d
            d = d.parent
        return None

    def summary(self):
        """Return a summary dict of found artifacts."""
        return {
            'sim_dir': str(self.sim_dir),
            'testname': self.testname,
            'tarmac': str(self.tarmac) if self.tarmac else None,
            'sim_log': str(self.sim_log) if self.sim_log else None,
            'adp_log': str(self.adp_log) if self.adp_log else None,
            'vcd': str(self.vcd) if self.vcd else None,
            'elf': str(self.elf) if self.elf else None,
            'memmap_h': str(self.memmap_h) if self.memmap_h else None,
        }
