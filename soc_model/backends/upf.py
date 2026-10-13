# Copyright 2026, SoC Labs (www.soclabs.org)
"""UPF (power-intent) backend — doc-07 M5.

Emits a Synopsys-AON-profile ``nanosoc_chip_pads_<tech>_<variant>.upf`` for the
multicore pad ring, reproducing the structure of the hand-written ASIC UPF
(``nanosoc_chip_pads.upf`` of the 44-pin flow):

  * TECH-CONSTANT header  (from ``pad_tech.upf``):
      - two supply sets (VDD_VSS, VDDACC_VSS), two-phase create + ``-update``,
      - power domains TOP (default) and ACCEL (omitted when its -elements root
        is not model-derivable — the multicore has no accelerator),
      - supply ports / nets + self-connects (VDDACC/VDD/VSS),
      - a single AON PST corner @ 1.08 V.

  * MODEL-DERIVED ``connect_supply_net`` block:
      - one VDD + one VSS line per PHYSICAL memory macro in the design,
      - derived by walking ``module.srams`` + ``instance.resolved_module`` (the
        SAME recursion as SoCSramBackend), prefixing the declared ``top_prefix``
        constant and appending the per-memory-kind leaf supply pin
        (``mem_power_pins`` : sram->VDD/VSS, rom->VDDE/VSSE).

The path LIST necessarily DIFFERS from the single-core hand file (different
memory set, no hard ROM macro, different inner-instance names) — that is
expected and reported, not faked into a byte match.

What is NOT model-derivable (reported via ``residuals()``):
  * ``top_prefix`` (template instance labels u_<chip_module>/u_soc, not nodes),
  * the leaf macro instance name + leaf pin names (inside the vendor macro),
  * the RTL genblk tail (gen_rf_*/gen_banked) chosen by sl_sram's generate,
  * the ACCEL domain -elements root (no accelerator instance in the multicore).

Generated file:
  - nanosoc_chip_pads_<tech>_<variant>.upf
"""

import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..model import Module, ResolvedSram, SramEntry
from ..utils import resolve_param_ref, write_if_changed


class SoCUpfBackend:
    """Generates the pad-ring UPF skeleton from a top Module + pad_tech.upf.

    Args:
      top_module : the inner SoC top (walked for memories from ``u_soc`` down).
      upf_desc   : the ``pad_tech.upf`` descriptor dict (tech-constant intent +
                   the top_prefix / leaf-pin constants).
      tech       : pad-tech name (filename suffix, e.g. ``generic``).
      variant    : pad-ring variant name (filename suffix, e.g. ``multicore``).
    """

    def __init__(self, top_module: Module, upf_desc: Dict[str, Any],
                 tech: str = '', variant: str = 'multicore'):
        self.top = top_module
        self.upf = upf_desc or {}
        self.tech = tech
        self.variant = variant
        self._residuals: List[str] = []

    # ------------------------------------------------------------------
    # model walk (memory supply paths) — mirrors SoCSramBackend._walk
    # ------------------------------------------------------------------
    def memory_paths(self) -> List[Dict[str, Any]]:
        """Walk the hierarchy collecting one entry per physical memory macro.

        Each entry: {path, kind, vdd_pin, vss_pin, addr_width, name}.

        ``path`` is the FULL hierarchical instance path to the leaf macro,
        slash-separated and prefixed with ``top_prefix``, WITHOUT the trailing
        ``/<pin>`` (callers append vdd_pin / vss_pin).  The leaf macro instance
        name (``macro_leaf``, default ``u_rf_sp_hdf``) is appended here.

        Only ``module.srams`` entries become connect lines; behavioural ROMs
        carry NO SramEntry, so they contribute nothing (matching the multicore,
        which has no hard ROM macro).
        """
        prefix = self.upf.get('top_prefix', '')
        leaf = self.upf.get('macro_leaf', 'u_rf_sp_hdf')
        pins = self.upf.get('mem_power_pins', {}) or {}
        sram_pins = pins.get('sram', {'vdd': 'VDD', 'vss': 'VSS'})

        raw: List[Tuple[str, ResolvedSram]] = []
        top_flat = {n: p.default for n, p in self.top.params.items()}
        self._walk(self.top, '', top_flat, raw)

        entries: List[Dict[str, Any]] = []
        for hier_path, sram in raw:
            # hier_path is dot-pathed below the top (no top name, no prefix);
            # build the full slash path: <prefix>/<inst/.../sram>/<leaf>
            inner = hier_path.replace('.', '/')
            segs = [s for s in (prefix, inner, leaf) if s]
            full = '/'.join(segs)
            entries.append({
                'path': full,
                'kind': 'sram',
                'vdd_pin': sram_pins.get('vdd', 'VDD'),
                'vss_pin': sram_pins.get('vss', 'VSS'),
                'addr_width': sram.addr_width,
                'name': sram.name,
            })
        return entries

    def _walk(self, module: Module, hier_path: str,
              effective_params: Dict[str, Any],
              entries: List[Tuple[str, ResolvedSram]]):
        """Recursive hierarchy walk — copy of SoCSramBackend._walk semantics.

        ``hier_path`` is the dot-path BELOW the top module (the top's own name is
        not included, so the emitted path starts at the first child instance —
        e.g. ``u_eth_ss_0.u_region_imem_0.u_mem``).
        """
        for sram in module.srams:
            aw = resolve_param_ref(sram.addr_width, effective_params)
            dw = resolve_param_ref(sram.data_width, effective_params)
            resolved = ResolvedSram(
                name=sram.name,
                addr_width=aw if isinstance(aw, int) else 14,
                data_width=dw if isinstance(dw, int) else 32,
                desc=sram.desc,
            )
            path = f"{hier_path}.{sram.name}" if hier_path else sram.name
            entries.append((path, resolved))

        for inst in module.instances:
            child = inst.resolved_module
            if not child:
                continue
            child_path = (f"{hier_path}.{inst.instance_name}"
                          if hier_path else inst.instance_name)
            child_defaults = {n: p.default for n, p in child.params.items()}
            overrides = {}
            for k, v in inst.params.items():
                overrides[k] = resolve_param_ref(v, effective_params)
            child_effective = {**child_defaults, **overrides}
            self._walk(child, child_path, child_effective, entries)

    # ------------------------------------------------------------------
    # render
    # ------------------------------------------------------------------
    def _filename(self) -> str:
        stem = 'nanosoc_chip_pads'
        if self.tech:
            stem += f'_{self.tech}'
        if self.variant:
            stem += f'_{self.variant}'
        return f'{stem}.upf'

    def render(self) -> str:
        """Render the full UPF text (deterministic; no jinja2 dependency)."""
        u = self.upf
        L: List[str] = []

        scope = u.get('scope', 'nanosoc_chip_pads')
        L.append(f'#Scope: {scope}')

        # --- supply sets (bare create) ---
        supply_sets = u.get('supply_sets', {}) or {}
        # Preserve insertion order but emit VDDACC_VSS first to match the hand
        # file ordering when both present.
        ss_order = list(supply_sets.keys())
        for name in ss_order:
            L.append(f'create_supply_set {name}')
        L.append('')

        # --- power domains ---
        domains = u.get('domains', []) or []
        for d in domains:
            name = d.get('name', '')
            ss = d.get('supply_set', '')
            root = d.get('root')
            L.append(f'#Domain: {name}')
            if root:
                L.append(
                    f'create_power_domain {name}  -supply {{primary {ss}}}  '
                    f'-elements {{{root}}}')
            elif name == 'TOP' or root is None and 'root' not in d:
                # default domain (no -elements)
                L.append(
                    f'create_power_domain {name}  -supply {{primary {ss}}}')
            else:
                # root explicitly null -> domain not derivable; omit + comment
                L.append(
                    f'# create_power_domain {name}  -supply {{primary {ss}}}  '
                    f'-elements {{<not model-derivable: no accelerator '
                    f'instance>}}')
                self._residuals.append(
                    f'domain {name} omitted: -elements root not model-derivable '
                    f'(no accelerator/expansion instance in the multicore)')
            L.append('')

        # --- associate supply sets to domain handles ---
        for d in domains:
            name = d.get('name', '')
            ss = d.get('supply_set', '')
            root = d.get('root')
            if name == 'TOP' or root:
                L.append(f'associate_supply_set {ss} -handle {name}.primary')
        L.append('')

        # --- supply ports / nets + self-connects ---
        ports = u.get('supply_ports', []) or []
        for p in ports:
            L.append(f'create_supply_port {p}')
        L.append('')
        for p in ports:
            L.append(f'create_supply_net {p}')
        L.append('')
        for p in ports:
            L.append(f'connect_supply_net {p} -ports {p}')
        L.append('')
        L.append('')

        # --- two-phase supply-set -update (functions) ---
        for name in ss_order:
            spec = supply_sets[name]
            power = spec.get('power', '')
            ground = spec.get('ground', '')
            L.append(
                f'create_supply_set {name} -function {{power {power}}} '
                f'-function {{ground {ground}}} -update')
        L.append('')
        L.append('')

        # --- MODEL-DERIVED memory connect block ---
        mems = self.memory_paths()
        if mems:
            for m in mems:
                L.append(
                    f"connect_supply_net {m['vdd_pin']} "
                    f"-ports {m['path']}/{m['vdd_pin']}")
            L.append('')
            for m in mems:
                L.append(
                    f"connect_supply_net {m['vss_pin']} "
                    f"-ports {m['path']}/{m['vss_pin']}")
            L.append('')
        else:
            L.append('# (no SRAM macros found in the model hierarchy)')
            L.append('')

        # --- PST (port states + pst + state) ---
        pst = u.get('pst', {}) or {}
        v = pst.get('voltage', 1.08)
        gv = pst.get('ground_voltage', 0.00)
        sname = pst.get('state_name', 'state1')
        pst_name = pst.get('pst_name', 'all_pst')
        pst_state = pst.get('pst_state', 'aon')
        pst_supplies = pst.get('supplies', ports)

        # port states: ground at gv, powers at v
        powers = {s.get('power') for s in supply_sets.values()}
        grounds = {s.get('ground') for s in supply_sets.values()}
        emitted_states: set = set()
        # ground(s) first (matches hand file: VSS then VDD then VDDACC)
        for p in pst_supplies:
            if p in grounds and p not in emitted_states:
                L.append(f'add_port_state {p} -state {{{sname} {gv:.2f}}}')
                emitted_states.add(p)
        for p in pst_supplies:
            if p in powers and p not in emitted_states:
                L.append(f'add_port_state {p} -state {{{sname} {v:.2f}}}')
                emitted_states.add(p)
        # any supply not classified -> treat as power
        for p in pst_supplies:
            if p not in emitted_states:
                L.append(f'add_port_state {p} -state {{{sname} {v:.2f}}}')
                emitted_states.add(p)
        L.append('')

        # create_pst (continuation-line list, matching the hand layout)
        L.append(f'create_pst {pst_name} -supplies {{ \\')
        for i, p in enumerate(pst_supplies):
            tail = '}' if i == len(pst_supplies) - 1 else ' \\'
            L.append(f'    {p}{tail}')
        L.append(f'add_pst_state {pst_state} -pst {pst_name} -state {{{sname}}}')
        L.append('')

        return '\n'.join(L) + '\n'

    def residuals(self) -> List[str]:
        """Bespoke / non-model-derivable items applied — the honest report.

        Populated during render() for the ACCEL domain; the constant
        deviations (top_prefix, leaf pins, genblk tail) are always reported.
        """
        r: List[str] = []
        if self.upf.get('top_prefix'):
            r.append(
                f"top_prefix declared constant (not model-derivable): "
                f"{self.upf['top_prefix']}")
        leaf = self.upf.get('macro_leaf', 'u_rf_sp_hdf')
        pins = self.upf.get('mem_power_pins', {}) or {}
        r.append(
            f"leaf macro/pins are vendor-macro constants (not derivable): "
            f"{leaf} {pins}")
        r.append(
            "genblk tail (gen_rf_*/gen_banked.gen_bank[b]) NOT emitted — it is "
            "the sl_sram RTL generate label, AW-dependent and outside the model")
        # Flag any memory whose AW banks into >1 physical rf_32k macro: the
        # model carries ONE SramEntry but the RTL instantiates N=2^(AW-15)
        # banked macros, so the single emitted connect line under-counts.
        for m in self.memory_paths():
            aw = m.get('addr_width')
            if isinstance(aw, int) and aw > 15:
                n = 2 ** (aw - 15)
                r.append(
                    f"banked memory {m['name']} (AW={aw}) is {n} physical "
                    f"rf_32k macros in RTL; the model has 1 SramEntry so only 1 "
                    f"connect pair is emitted for {m['path']}")
        r.extend(self._residuals)
        return r

    def generate(self, rtl_dir: Path,
                 flist_dir: Path = None) -> Optional[Path]:
        """Write the UPF file (and an inline flist).  Returns the .upf path.

        Self-contained (no jinja2): the UPF is rendered by ``render()``.
        """
        rtl_dir = Path(rtl_dir)
        rtl_dir.mkdir(parents=True, exist_ok=True)

        content = self.render()
        upf_path = rtl_dir / self._filename()
        write_if_changed(upf_path, content)

        if flist_dir is None:
            flist_dir = rtl_dir.parent / 'flist'
        flist_dir = Path(flist_dir)
        flist_dir.mkdir(parents=True, exist_ok=True)
        flist_content = (
            f"// Auto-generated UPF file list for {self._filename()}\n"
            f"$SOCLABS_NANOSOC_SOC_DIR/build_soc/rtl/{self._filename()}\n"
        )
        flist_path = flist_dir / f'{Path(self._filename()).stem}.flist'
        write_if_changed(flist_path, flist_content)

        return upf_path
