"""DMA-250 render backend for nanosoc_gen.

Renders the Arm CoreLink DMA-250 (CG097) RTL by invoking Arm's `generate`
script, in the same spirit that SoCAhbBackend invokes ARM's BuildBusMatrix.pl
for the bus matrix:

  * Detect whether the SoC model instantiates a DMA-250 wrapper module
    (module_name containing "dma_250"). If none, no-op.
  * Run `<dma250_logical>/generate --config config/<cfg> --output <out>` via
    subprocess (cwd = the IP-library logical dir, output OUTSIDE it), logging
    stdout/stderr and warning (non-fatally) on any failure — exactly the
    failure style of _run_build_bus_matrix.
  * Flatten the rendered design.vc (which uses -F includes + paths relative to
    each .vc file) into an absolute-path flist the project flist can -f include.

The hand-written SoC glue (dma_250_ahb wrapper, AHB5->AHB-Lite shim, context
SRAM, LPI controller) is ordinary project RTL listed in the project flist; this
backend only renders the configurable Arm core and emits its flist.
"""

import os
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

# Default location of the DMA-250 (CG097) logical deliverable, relative to
# ARM_IP_LIBRARY_PATH. Overridable via SOCLABS_DMA250_DIR or the constructor.
_DMA250_DEFAULT_RELPATH = (
    'DMA-250/CG097-r0p0-00eac0-6/CG097-BU-50000-r0p0-00eac0/dma250/logical'
)
_DEFAULT_CONFIG = 'cfg_min.yaml'
_MODULE_MATCH = 'dma_250'


def _config_name(config_file: str) -> str:
    """CONFIG_NAME suffix for a config file (cfg_min.yaml -> CFG_MIN).

    Reads CONFIG_NAME from the YAML if available, else derives from the stem.
    """
    return Path(config_file).stem.upper()


def _flatten_design_vc(vc_path: Path, out: List[str], seen_inc: set):
    """Resolve a rendered design.vc (-F includes + relative paths) to absolute."""
    base = vc_path.parent
    for raw in vc_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith('//'):
            continue
        if line in ('-F', '-f'):
            continue
        if line.startswith('-F ') or line.startswith('-f '):
            inc = line.split(None, 1)[1].strip()
            inc_p = Path(inc) if os.path.isabs(inc) else (base / inc).resolve()
            _flatten_design_vc(inc_p, out, seen_inc)
            continue
        if line.startswith('+incdir+'):
            d = line[len('+incdir+'):]
            d_p = d if os.path.isabs(d) else str((base / d).resolve())
            key = '+incdir+' + d_p
            if key not in seen_inc:
                seen_inc.add(key)
                out.append(key)
            continue
        if line.startswith(('+', '-')):
            out.append(line)
            continue
        f_p = line if os.path.isabs(line) else str((base / line).resolve())
        out.append(f_p)


class SoCDma250Backend:
    """Render the Arm DMA-250 core for any DMA-250 instances in the SoC model."""

    def __init__(self, top_module, arm_ip_library_path: Optional[str] = None,
                 dma250_dir: Optional[str] = None,
                 config_file: str = _DEFAULT_CONFIG):
        self.top = top_module
        self.arm_ip_library_path = arm_ip_library_path
        self.dma250_dir = dma250_dir
        self.config_file = config_file

    # -- hierarchy walk -----------------------------------------------------
    def _find_dma250_instances(self) -> List[str]:
        """Return module_names of DMA-250 instances anywhere in the hierarchy."""
        found, visited = [], set()

        def walk(module):
            if module is None or id(module) in visited:
                return
            visited.add(id(module))
            for inst in getattr(module, 'instances', []):
                mn = getattr(inst, 'module_name', '') or ''
                if _MODULE_MATCH in mn:
                    found.append(mn)
                walk(getattr(inst, 'resolved_module', None))

        walk(self.top)
        return found

    def _resolve_dma250_dir(self) -> Optional[Path]:
        if self.dma250_dir:
            return Path(self.dma250_dir)
        env = os.environ.get('SOCLABS_DMA250_DIR')
        if env:
            return Path(env)
        arm = self.arm_ip_library_path or os.environ.get('ARM_IP_LIBRARY_PATH')
        if arm:
            return Path(arm) / _DMA250_DEFAULT_RELPATH
        return None

    # -- entry point (mirrors SoCAhbBackend.generate_all) -------------------
    def generate_all(self, build_dir: Path) -> List[Tuple[str, str]]:
        instances = self._find_dma250_instances()
        if not instances:
            print("  No DMA-250 instances found, skipping render")
            return []

        logical = self._resolve_dma250_dir()
        if logical is None:
            print("  WARNING: ARM_IP_LIBRARY_PATH/SOCLABS_DMA250_DIR not set, "
                  "skipping DMA-250 render")
            return []
        gen_script = logical / 'generate'
        if not gen_script.exists():
            print(f"  WARNING: DMA-250 generate script not found at {gen_script}")
            return []

        cfg_name = _config_name(self.config_file)
        rtl_dir = build_dir / 'rtl' / 'dma250'
        out_dir = rtl_dir / f'rendered_{cfg_name}'
        flist_dir = build_dir / 'flist'
        logs_dir = build_dir / 'rtl' / 'dma250' / 'logs'
        for d in (rtl_dir, flist_dir, logs_dir):
            d.mkdir(parents=True, exist_ok=True)

        config_path = logical / 'config' / self.config_file
        log_file = logs_dir / f'dma250_{cfg_name}.log'
        cmd = ['python3', './generate',
               '--config', f'config/{self.config_file}',
               '--output', str(out_dir.resolve())]

        print(f"  Running DMA-250 generate for {len(instances)} instance(s) "
              f"(config {self.config_file})...")
        try:
            result = subprocess.run(
                cmd,
                cwd=str(logical),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=300,
            )
            with open(log_file, 'w') as f:
                f.write(f"Command: {' '.join(cmd)}\n\n")
                if result.stdout:
                    f.write("=== STDOUT ===\n" + result.stdout)
                if result.stderr:
                    f.write("\n=== STDERR ===\n" + result.stderr)
            if result.returncode != 0:
                print(f"  WARNING: DMA-250 generate exited with code "
                      f"{result.returncode}")
                print(f"  Log: {log_file}")
                return []
        except FileNotFoundError:
            print("  WARNING: python3 not found, cannot run DMA-250 generate")
            return []
        except subprocess.TimeoutExpired:
            print("  WARNING: DMA-250 generate timed out after 300s")
            return []

        # Locate the rendered top design.vc and flatten it to an absolute flist.
        design_vc = (out_dir / 'logical' / f'dma250_top_{cfg_name}'
                     / 'verilog' / 'design.vc')
        if not design_vc.exists():
            print(f"  WARNING: rendered design.vc not found at {design_vc}")
            return []

        flat: List[str] = []
        _flatten_design_vc(design_vc, flat, set())
        header = (f"// Auto-generated by SoCDma250Backend — rendered DMA-250 "
                  f"({cfg_name}) core file-set.\n"
                  f"// Regenerate via `make soc`. Do not edit by hand.\n")
        flist_path = flist_dir / 'dma250_rendered.flist'
        flist_path.write_text(header + '\n'.join(flat) + '\n')

        print(f"  DMA-250 rendered: {out_dir}")
        print(f"  DMA-250 flist:    {flist_path}")
        return [(f'dma250_{cfg_name}', str(out_dir))]
