"""SoC config package backend — generates a SystemVerilog package of design parameters.

Replaces gen_defines.v by producing a package containing all parameterizable
design and simulation configuration as localparams. Parameters that cannot be
expressed as SV params (e.g. POWER_PINS, CORTEX_M0) remain as compiler defines.

Generated file:
  - build_soc/rtl/nanosoc_soc_config_pkg.sv
"""

import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..model import Module
from ..utils import write_if_changed

try:
    from jinja2 import Environment, FileSystemLoader
except ImportError:
    Environment = None
    FileSystemLoader = None

_BACKEND_DIR = Path(__file__).resolve().parent
_TEMPLATE_DIR = _BACKEND_DIR / 'templates'

# Parameters to include in the design config section (from YAML params).
# Order matters for readability of output.
_DESIGN_PARAMS = [
    # DMA configuration
    'DMAC_0_TYPE',
    'DMAC_1_TYPE',
    'DMAC_0_CHANNEL_NUM',
    'DMAC_1_CHANNEL_NUM',
    # Memory configuration
    'BOOTROM_ADDR_W',
    'IMEM_RAM_ADDR_W',
    'IMEM_RAM_DATA_W',
    'DMEM_RAM_ADDR_W',
    'DMEM_RAM_DATA_W',
    'SRAM_0_RAM_ADDR_W',
    'SRAM_0_RAM_DATA_W',
    'SRAM_1_RAM_ADDR_W',
    'SRAM_1_RAM_DATA_W',
    # Feature enables
    'IMEM_0_RAM_PRELOAD',
    'ACCELERATOR_SUBSYSTEM',
    # CPU configuration
    'CLKGATE_PRESENT',
    'BE',
    'BKPT',
    'DBG',
    'NUMIRQ',
    'SMUL',
    'SYST',
    'WIC',
    'WICLINES',
    'WPT',
    'RESET_ALL_REGS',
    'INCLUDE_JTAG',
    # Bus widths
    'SYS_ADDR_W',
    'SYS_DATA_W',
    'APB_ADDR_W',
    'APB_DATA_W',
    # FTDI
    'FT1248_WIDTH',
    'FT1248_CLKON',
    'FT1248_CLKDIV',
    # System identification
    'SYSTABLE_BASE',
    'SOCLABS_JEPID',
    'NANOSOC_PARTNUMBER',
    'NANOSOC_REVISION',
]

# Simulation-only parameters (set via config overrides, not in YAML design params)
_SIM_PARAM_DEFAULTS = {
    'FAST_LOAD': (0, 'Fast memory load mode (1=fast, 0=normal)'),
    'VCD_ENABLE': (0, 'VCD waveform dump enable (1=enabled, 0=disabled)'),
}


@dataclass
class ConfigParam:
    """A parameter entry for the config package."""
    name: str
    value: Any
    type: str = 'int'
    desc: str = ''


class SoCConfigPkgBackend:
    """Generates the SoC configuration package from the module model."""

    def __init__(self, top_module: Module):
        self.top = top_module

    def generate(self, rtl_dir: Path, flist_dir: Path = None,
                 config_overrides: Optional[Dict[str, Any]] = None,
                 ) -> Optional[Path]:
        """Generate nanosoc_soc_config_pkg.sv.

        Args:
            rtl_dir: Directory for generated RTL output.
            flist_dir: Directory for generated filelist output.
            config_overrides: Optional key=value overrides (from Makefile).
                              Can override YAML defaults or set sim params.

        Returns:
            Path to generated file, or None if jinja2 unavailable.
        """
        if Environment is None:
            print("  WARNING: jinja2 not available — skipping config package generation")
            return None

        overrides = config_overrides or {}
        rtl_dir = Path(rtl_dir)
        rtl_dir.mkdir(parents=True, exist_ok=True)

        # Build design params from YAML
        design_params = []
        flat = self.top.flat_params
        for pname in _DESIGN_PARAMS:
            if pname in self.top.params:
                p = self.top.params[pname]
                value = overrides.get(pname, p.default)
                ptype = p.type if p.type != 'str' else 'str'
                design_params.append(ConfigParam(
                    name=pname,
                    value=value,
                    type=ptype,
                    desc=p.desc,
                ))
            elif pname in overrides:
                # Parameter not in YAML but provided as override
                design_params.append(ConfigParam(
                    name=pname,
                    value=overrides[pname],
                    desc='',
                ))

        # Build simulation params
        sim_params = []
        for pname, (default, desc) in _SIM_PARAM_DEFAULTS.items():
            value = overrides.get(pname, default)
            sim_params.append(ConfigParam(
                name=pname,
                value=value,
                desc=desc,
            ))

        # Render template
        env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )

        pkg_name = f'{self.top.name}_soc_config_pkg'
        template = env.get_template('nanosoc_soc_config_pkg.sv.j2')
        content = template.render(
            package_name=pkg_name,
            design_params=design_params,
            sim_params=sim_params,
            source_yaml=self.top.source_file or 'nanosoc_m0_soc.yaml',
            generated_date=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        )

        output_path = rtl_dir / f'{pkg_name}.sv'
        write_if_changed(output_path, content)

        # Generate Verilog-compatible include file (replaces gen_defines.v)
        # This uses `define statements so .v files can access config values
        vh_path = rtl_dir / f'{self.top.name}_soc_config.vh'
        self._write_verilog_header(design_params, sim_params, vh_path)

        # Generate flist
        if flist_dir is not None:
            flist_dir = Path(flist_dir)
            flist_dir.mkdir(parents=True, exist_ok=True)
            flist_path = flist_dir / f'{pkg_name}.flist'
            # Emit a layout-agnostic make-variable path (matches the toplevel /
            # system / chip / AHB backends) rather than an absolute path, so the
            # flist is portable and byte-stable across checkouts. The package .sv
            # lives at build_soc/rtl/<pkg_name>.sv in a real build.
            write_if_changed(flist_path,
                f"// Auto-generated filelist for {pkg_name}\n"
                f"$(SOCLABS_NANOSOC_SOC_DIR)/build_soc/rtl/{pkg_name}.sv\n"
            )

        return output_path

    def _write_verilog_header(self, design_params: List[ConfigParam],
                              sim_params: List[ConfigParam],
                              output_path: Path):
        """Write a Verilog-compatible include file with `define statements.

        This file serves the same role as gen_defines.v but is generated
        from YAML parameters rather than Makefile logic.
        """
        lines = [
            '//-----------------------------------------------------------------------------',
            '// AUTO-GENERATED SoC Configuration (Verilog include)',
            '// Replaces gen_defines.v — generated from YAML system description.',
            f'// Generated: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}',
            '// DO NOT EDIT — regenerate with soc_model',
            '//-----------------------------------------------------------------------------',
            '',
        ]

        lines.append('// Design configuration')
        for p in design_params:
            if p.type == 'str':
                lines.append(f'`define {p.name} "{p.value}"')
            else:
                lines.append(f'`define {p.name} {p.value}')

        if sim_params:
            lines.append('')
            lines.append('// Simulation configuration')
            for p in sim_params:
                lines.append(f'`define {p.name} {p.value}')

        lines.append('')
        write_if_changed(output_path, '\n'.join(lines))
