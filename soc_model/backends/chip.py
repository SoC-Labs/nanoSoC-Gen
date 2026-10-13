"""Chip-level wrapper backend — generates nanosoc_chip.v and nanosoc_chip_pads.v.

Produces the chip integration and pad-level wrappers by extracting GPIO port
information from the system module's interfaces.

Generated files:
  - nanosoc_chip.v      — chip-level wrapper (clock/reset/test muxing, system instantiation)
  - nanosoc_chip_pads.v — pad-level wrapper (I/O pad cells, chip_cfg, chip instantiation)
"""

import datetime
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..model import Interface, Module
from ..utils import resolve_param_ref, flatten_params, write_if_changed
from .protocol_utils import SWD_SIGNALS
from .system import boundary_net_map, boundary_port_name

try:
    from jinja2 import Environment, FileSystemLoader
except ImportError:
    Environment = None
    FileSystemLoader = None

_BACKEND_DIR = Path(__file__).resolve().parent
_TEMPLATE_DIR = _BACKEND_DIR / 'templates'

# DMAC_*_TYPE encoding shared with nanosoc_soc_config_pkg (0=None, 1=PL230,
# 2=DMA350) and the compile-time `define override ladder the hand-maintained
# chip/chip/verilog/nanosoc_chip.v has always used. The chip is a plain
# Verilog-2001 .v (Vivado's IP packager rejects `pkg::` in it), so the type is
# resolved here as a localparam ladder instead of a package-scope reference.
_DMA_TYPE_LABELS = {0: 'None', 1: 'PL230 single-port', 2: 'DMA350 dual-port'}
_DMA_TYPE_DEFINES = {
    'DMAC_0_TYPE': (('DMAC_DMA350', 2), ('DMAC_0_PL230', 1)),
    'DMAC_1_TYPE': (('DMAC_1_PL230', 1),),
}

# Memory address widths the chip passes down to u_system. The system wrapper
# (soc_system.sv.j2) declares each with its OWN YAML's default and hands it on
# to the core, the CPU subsystem and every memory region, so without an
# override on u_system the elaborated memories are the system's size, whatever
# the top YAML says. The top YAML is the one the firmware backend (linker
# MEMORY, nanosoc_memmap.h) and the config package read: a top that grew IMEM
# to 128 KB linked a 128 KB image into a 16 KB memory, silently.
#
# EVERY width the system declares is passed, always, even when the two YAMLs
# agree. The system module a flow compiles is not necessarily the one this
# generator renders: a project may compile a hand-written nanosoc_system.v
# (nanosoc_arch_tech/rtl/src/system/) with a third copy of the defaults, which
# no comparison here can see. Stating every width on u_system makes the chip
# the one place the memory sizes are decided.
_MEM_WIDTH_PARAM_RE = re.compile(r'^(?:BOOTROM_ADDR_W|[A-Z][A-Z0-9_]*_RAM_ADDR_W)$')

# Wire-bus groups passed straight through to the die boundary, as
# (label, lower-case name prefix, port-list note). The label and note are
# written into the generated comments, so changing them changes every chip.
#   qspi_  the XiP flash controller: sclk, csn, and io_o/_i/_e per lane
#   spi_   the PL022 master bond-out: SCLK, SS, MOSI, MISO (upper-case in the
#          system YAML; the prefix match ignores case, and 'qspi_' never
#          matches 'spi_' because the match is anchored at the start)
_PASSTHROUGH_GROUPS = (
    ('QSPI flash', 'qspi_', 'o/i/e per lane, plain chip port (the top owns the pad)'),
    ('SPI master', 'spi_', 'plain chip port (the top owns the pad)'),
)

# System ports soc_chip.v.j2 connects by LITERAL name on u_system. The
# "nothing is dropped" check treats them as handled; a test pins this set to
# the template, so a port added there and not here is a test failure, not a
# false warning.
_TEMPLATE_SYS_PORTS = frozenset({
    'SYS_CLK', 'SYS_SYSRESETn', 'SYS_XTALCLK_OUT', 'SYS_SCANENABLE',
    'SYS_TESTMODE', 'SYS_SCANINHCLK', 'SYS_SCANOUTHCLK',
})


class ChipParamError(ValueError):
    """A parameter the chip cannot state faithfully: a memory width that is not
    an integer, or a top-YAML value the chip does not pass to u_system and the
    system YAML contradicts (the RTL would elaborate the system's value)."""


class SoCChipBackend:
    """Generates chip-level and pad-level wrappers from the system module."""

    def __init__(self, system_module: Module, core_module: Optional[Module] = None,
                 config_overrides: Optional[Dict[str, Any]] = None,
                 allow_param_mismatch: bool = False,
                 yaml_paths: Optional[Dict[str, str]] = None):
        self.system = system_module
        self.core = core_module
        # --allow-param-mismatch: report, but do not fail on, a parameter the
        # top and system YAMLs disagree on that the chip does not pass.
        self.allow_param_mismatch = allow_param_mismatch
        self._mismatch_reported = False
        # The YAML files, for the messages: {'core': path, 'system': path}.
        self.yaml_paths = dict(yaml_paths or {})
        # `--config-override NAME=val` (the same dict the config-package backend
        # gets) so the chip's DMAC_*_TYPE localparam default tracks the package
        # exactly as the old `pkg::` reference did.
        self.config_overrides = dict(config_overrides or {})
        self.flat_params = flatten_params(
            {n: p.default for n, p in system_module.params.items()}
        )

    def generate(self, rtl_dir: Path, flist_dir: Path = None) -> Tuple[Optional[Path], Optional[Path]]:
        """Generate nanosoc_chip.v and nanosoc_chip_pads.v in rtl_dir.

        Returns (chip_path, chip_pads_path).
        """
        if Environment is None:
            print("  WARNING: jinja2 not available — skipping chip wrapper generation")
            return None, None

        rtl_dir = Path(rtl_dir)
        rtl_dir.mkdir(parents=True, exist_ok=True)

        env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )

        ctx = self._build_context()

        # Generate nanosoc_chip.v
        chip_template = env.get_template('soc_chip.v.j2')
        chip_content = chip_template.render(**ctx)
        chip_path = rtl_dir / f'{ctx["module_name"]}.v'
        write_if_changed(chip_path, chip_content)

        # Generate nanosoc_chip_pads.v
        pads_template = env.get_template('soc_chip_pads.v.j2')
        pads_ctx = dict(ctx)
        pads_ctx['module_name'] = f'{ctx["module_name"]}_pads'
        pads_ctx['chip_module_name'] = ctx['module_name']
        pads_content = pads_template.render(**pads_ctx)
        pads_path = rtl_dir / f'{ctx["module_name"]}_pads.v'
        write_if_changed(pads_path, pads_content)

        # Generate flist
        if flist_dir is None:
            flist_dir = rtl_dir.parent / 'flist'
        flist_dir = Path(flist_dir)
        flist_dir.mkdir(parents=True, exist_ok=True)

        flist_content = f"// Auto-generated file list for chip wrappers\n"
        flist_content += f"$SOCLABS_NANOSOC_SOC_DIR/build_soc/rtl/{ctx['module_name']}.v\n"
        flist_content += f"$SOCLABS_NANOSOC_SOC_DIR/build_soc/rtl/{ctx['module_name']}_pads.v\n"
        flist_path = flist_dir / f'{ctx["module_name"]}_chip.flist'
        write_if_changed(flist_path, flist_content)

        return chip_path, pads_path

    def _build_context(self) -> Dict[str, Any]:
        """Build the template context from the system module's interfaces."""
        now = datetime.datetime.now()

        # Extract GPIO ports from system module interfaces
        gpio_ports = []
        for iface in self.system.interfaces:
            if iface.type == 'gpio':
                width = iface.params.get('WIDTH', 16)
                width = resolve_param_ref(width, self.flat_params)
                gpio_ports.append({
                    'name': iface.name,
                    'width': int(width),
                })

        # Extract the CoreSight SWJ-DAP boundary group from the system
        # interfaces. The DAP pins are declared as individual `wire` ifaces
        # sharing the `dap_` prefix (dap_swclktck, dap_swdo, ...). They are
        # brought out to the chip boundary verbatim and passed straight through
        # to the system instance -- no pad cells (the FPGA wrapper resolves the
        # SWDIO tristate / JTAG in fabric). A named-prefix group keeps this
        # general (mirrors gpio) without hard-coding each pin. Because these are
        # plain `wire` ifaces, system.py emits the boundary port verbatim as
        # iface.name, so the lower-case dap_* names line up across
        # core -> system -> chip with no case-mismatch remap.
        dap_ports = []
        for iface in self.system.interfaces:
            if iface.type == 'wire' and iface.name.lower().startswith('dap_'):
                width = resolve_param_ref(
                    iface.params.get('WIDTH', 1), self.flat_params)
                dap_ports.append({
                    'name': iface.name,
                    'direction': 'input' if iface.is_input else 'output',
                    'width': int(width),
                })

        # Pass-through groups: `wire` ifaces sharing a prefix, each a plain
        # port on the chip and on the pads module and a connection on u_system,
        # exactly like the dap_ group above. No pad cell: these SoC buses are
        # unidirectional or split o/i/e per lane and never `inout`, so the ASIC
        # or FPGA top that owns the pads resolves any tristate. Each port
        # carries its range (the QSPI IO lanes are 4 bits). Before these groups
        # existed, a system that declared such wires got them silently dropped
        # at the chip boundary - see _PASSTHROUGH_GROUPS.
        passthrough_groups = []
        for label, prefix, note in _PASSTHROUGH_GROUPS:
            ports = []
            for iface in self.system.interfaces:
                if iface.type == 'wire' and iface.name.lower().startswith(prefix):
                    width = int(resolve_param_ref(
                        iface.params.get('WIDTH', 1), self.flat_params))
                    ports.append({
                        'name': iface.name,
                        'direction': 'input' if iface.is_input else 'output',
                        'width': width,
                        'range': f'[{width - 1}:0]' if width > 1 else '',
                    })
            if ports:
                passthrough_groups.append({'label': label, 'note': note, 'ports': ports})

        # Serial-Wire Debug: an `swd`-type interface on the system module is
        # the single-core debug path -- the Cortex-M0 SWDI/SWCLK/SWDO/SWDOEN
        # go straight to the SWDIO/SWDCLK bond-out pads. The boundary nets are
        # named exactly as system.py declares them (the core instance's YAML
        # `conn:`, e.g. CPU_0_SWDI), so chip -> system -> core is one net.
        # Independent of the dap_ group above: a system with neither keeps the
        # legacy pads inert; one with both bonds each to its own pins.
        net_map = boundary_net_map(self.system)
        swd_ports = []
        for iface in self.system.interfaces:
            if iface.type != 'swd':
                continue
            base = iface.name[:-4] if iface.name.endswith('_swd') else iface.name
            entry = {'name': iface.name, 'to_pads': not swd_ports}
            for sig_name, _dir, _width in SWD_SIGNALS:
                entry[sig_name] = boundary_port_name(f'{base}_{sig_name}', net_map)
            # column the `assign` LHS on the longest name (matches the
            # hand-maintained file: CPU_0_SWCLK / CPU_0_SWDI  / swdio_o    )
            entry['lhs_w'] = max(len(entry['swclk']), len(entry['swdi']), len('swdio_o'))
            swd_ports.append(entry)
        if len(swd_ports) > 1:
            print(f"  WARNING: {len(swd_ports)} swd interfaces on {self.system.name}; "
                  f"only '{swd_ports[0]['name']}' is bonded to the SWDIO/SWDCLK pads, "
                  f"the others are held idle")

        # NOTHING IS DROPPED SILENTLY. Every system interface must be one of
        # the kinds above or a port soc_chip.v.j2 wires by literal name; any
        # other never reaches the die and is said so here. This is the check
        # that would have caught both the QSPI and the SPI pins, which the
        # chip lost for months with no message.
        grouped = {p['name'] for g in passthrough_groups for p in g['ports']}
        for iface in self.system.interfaces:
            if iface.type in ('gpio', 'swd'):
                continue
            if iface.type == 'wire' and (iface.name in _TEMPLATE_SYS_PORTS
                                         or iface.name.lower().startswith('dap_')
                                         or iface.name in grouped):
                continue
            print(f"  WARNING: {self.system.name} interface '{iface.name}' "
                  f"(type {iface.type}) is not bonded to the chip boundary - no "
                  f"chip-backend rule handles it, so it never leaves the die")

        # Derive chip module name from system module name
        # nanosoc_system -> nanosoc_chip
        chip_name = self.system.name.replace('_system', '_chip')

        # Core module name for config package reference
        core_name = self.core.name if self.core else chip_name.replace('_chip', '')

        # Only override DMA-type params on u_system when the system module
        # actually declares them (legacy ss_dma SoCs). DMA-250 SoCs drop
        # these params, so emitting the override would bind non-existent
        # pins and reference config-package members that no longer exist.
        dma_type_params = []
        for pn in ('DMAC_0_TYPE', 'DMAC_1_TYPE'):
            if pn not in self.system.params:
                continue
            default = self._dma_type_default(pn)
            dma_type_params.append({
                'name': pn,
                'cfg': f'{pn}_CFG',
                'default': default,
                'default_label': _DMA_TYPE_LABELS.get(default, f'type {default}'),
                'defines': [{'macro': m, 'value': v,
                             'label': _DMA_TYPE_LABELS.get(v, f'type {v}')}
                            for m, v in _DMA_TYPE_DEFINES.get(pn, ())],
            })
        mem_width_params = self._mem_width_params()
        self._check_unpassed_params(self._passed_params())

        if dma_type_params and all(p['default'] == 0 for p in dma_type_params):
            dma_defaults_desc = '0 (no DMA)'
        else:
            dma_defaults_desc = ', '.join(f"{p['name']}={p['default']}"
                                          for p in dma_type_params)

        return {
            'module_name': chip_name,
            'core_module_name': core_name,
            'system_module_name': self.system.name,
            'dma_type_params': dma_type_params,
            'dma_defaults_desc': dma_defaults_desc,
            'mem_width_params': mem_width_params,
            'system_param_overrides': dma_type_params + mem_width_params,
            'gpio_ports': gpio_ports,
            'dap_ports': dap_ports,
            'passthrough_groups': passthrough_groups,
            'swd_ports': swd_ports,
            'generated_date': now.strftime('%Y-%m-%d %H:%M:%S'),
            'generated_year': now.strftime('%Y'),
        }

    def _dma_type_default(self, pname: str) -> int:
        """Resolved DMAC_*_TYPE default: override > top YAML > system YAML > 0.

        Mirrors SoCConfigPkgBackend (override, else the top module's param
        default) so the chip localparam and the config package agree; the
        system module's own default is the fallback when the top does not
        declare the param (SoCChipBackend built without a core module).
        """
        if pname in self.config_overrides:
            raw = self.config_overrides[pname]
        elif self.core is not None and pname in self.core.params:
            raw = self.core.params[pname].default
        else:
            raw = self.system.params[pname].default
        raw = resolve_param_ref(raw, self.flat_params)
        try:
            return int(raw)
        except (TypeError, ValueError):
            print(f"  WARNING: {pname} default {raw!r} is not an integer; "
                  f"chip localparam falls back to 0 (None)")
            return 0

    # ------------------------------------------------------------------
    # Memory widths
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve(value: Any, params: Dict[str, Any]) -> Any:
        """resolve_param_ref until it stops changing (a "$A" -> "$B" chain)."""
        for _ in range(16):
            nxt = resolve_param_ref(value, params)
            if nxt == value:
                break
            value = nxt
        return value

    def _param_int(self, module: Module, pname: str, raw: Any) -> Optional[int]:
        value = self._resolve(raw, flatten_params(
            {n: p.default for n, p in module.params.items()}))
        try:
            # a --config-override arrives as text, and may be hex ('0x11')
            return int(value, 0) if isinstance(value, str) else int(value)
        except (TypeError, ValueError):
            raise ChipParamError(
                f"{module.name}.{pname} = {value!r} is not an integer: the chip "
                f"cannot set it on u_system") from None

    def _mem_width_params(self) -> List[Dict[str, Any]]:
        """Every memory address width the system declares, in its order, with
        the value to set on u_system.

        Value: `--config-override` > top YAML (the core module) > the system
        YAML's own default -- the same precedence as the DMA types and the
        config package. All of them are passed (see _MEM_WIDTH_PARAM_RE). A
        width the system module does not declare cannot be passed and is not.
        """
        out = []
        for pname, sp in self.system.params.items():
            if not _MEM_WIDTH_PARAM_RE.match(pname):
                continue
            have = self._param_int(self.system, pname, sp.default)
            if pname in self.config_overrides:
                src = '--config-override'
                want = self._param_int(self.system, pname, self.config_overrides[pname])
            elif self.core is not None and pname in self.core.params:
                src = f'{self.core.name} YAML'
                want = self._param_int(self.core, pname, self.core.params[pname].default)
            else:
                src = f'{self.system.name} YAML'
                want = have
            out.append({'name': pname, 'cfg': f'{pname}_CFG', 'value': want,
                        'system_default': have, 'source': src})
        return out

    def _passed_params(self) -> set:
        """The parameters the chip sets on u_system: the DMA types and the
        memory widths the system module declares."""
        return ({pn for pn in ('DMAC_0_TYPE', 'DMAC_1_TYPE') if pn in self.system.params}
                | {pn for pn in self.system.params if _MEM_WIDTH_PARAM_RE.match(pn)})

    def check_params(self) -> List[str]:
        """Validate, writing nothing: every memory width resolves to an integer,
        and the top and system YAMLs (with --config-override applied) agree on
        every parameter the chip does not pass. Raises ChipParamError, or
        returns the findings --allow-param-mismatch let through. The CLI runs
        this before ANY backend writes a file, so a refused run leaves an
        existing build directory as it was."""
        self._mem_width_params()
        return self._check_unpassed_params(self._passed_params())

    def _where(self, which: str) -> str:
        module = self.core if which == 'core' else self.system
        path = self.yaml_paths.get(which) or module.source_file or '?'
        role = 'top' if which == 'core' else 'system'
        return f"{role} YAML {path} (module {module.name})"

    @staticmethod
    def _show(value: Any, pname: str) -> str:
        """A value for a message: addresses (and anything large) in hex."""
        if isinstance(value, bool) or not isinstance(value, int):
            return repr(value)
        if value >= 0x10000 or re.search(r'(BASE|ADDR|ADDRESS)$', pname):
            return f"0x{value:08X}"
        return str(value)

    @staticmethod
    def _norm(value: Any) -> Any:
        if isinstance(value, str):
            try:
                return int(value, 0)
            except ValueError:
                return value
        return value

    def _check_unpassed_params(self, passed: set) -> List[str]:
        """A parameter the top and system YAMLs disagree on, which the chip does
        not pass, is an ERROR: the RTL would elaborate the system's value while
        the config package and the firmware follow the top's. The top's value is
        the EFFECTIVE one, --config-override applied, as the config package
        takes it. With allow_param_mismatch (--allow-param-mismatch) it is
        reported only (once)."""
        if self.core is None:
            return []
        core_flat = flatten_params({n: p.default for n, p in self.core.params.items()})
        found = []
        for pname, sp in self.system.params.items():
            if pname in passed:
                continue
            if pname in self.config_overrides:
                core_v, how = self.config_overrides[pname], ' (--config-override)'
            elif pname in self.core.params:
                core_v, how = self._resolve(self.core.params[pname].default, core_flat), ''
            else:
                continue
            sys_v = self._resolve(sp.default, self.flat_params)
            if self._norm(core_v) != self._norm(sys_v):
                cv, sv = self._show(self._norm(core_v), pname), self._show(self._norm(sys_v), pname)
                found.append(f"{pname}: {cv}{how} in the {self._where('core')}, "
                             f"{sv} in the {self._where('system')}; the chip does not pass "
                             f"{pname} to u_system, so the RTL elaborates {sv}")
        if not found:
            return []
        if self.allow_param_mismatch:
            if not self._mismatch_reported:
                for f in found:
                    print(f"  WARNING: {f} (allowed by --allow-param-mismatch)")
                self._mismatch_reported = True
            return found
        raise ChipParamError(
            "the top and system YAMLs disagree on parameters the chip does not pass:\n    "
            + "\n    ".join(found)
            + "\n  Make the two YAMLs agree, or pass --allow-param-mismatch to render anyway.")
