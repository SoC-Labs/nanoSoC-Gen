"""Firmware configuration backend — generates linker scripts, C headers, Makefiles, and ADP files.

Produces the same output files as the standalone gen_firmware_config.py, but driven
directly from the soc_model data structures and the firmware: section of the YAML.

Generated files:
  - Linker script MEMORY{} fragments (.ld) per profile
  - C header with memory base address defines (.h)
  - Makefile include with address variables (.mk)
  - Verilog header with ADP upload address (.vh)
  - Python module with ADP address constants (.py)
"""

import contextlib
import datetime
import io
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..model import (
    Firmware, Interconnect, InterconnectInitiator, InterconnectInitiatorTarget,
    InterconnectTarget, LinkerProfile, LinkerRegion, Module,
)
from ..utils import linker_origin, resolve_param_ref, flatten_params, write_if_changed

try:
    from jinja2 import Environment, FileSystemLoader
except ImportError:
    Environment = None
    FileSystemLoader = None

_BACKEND_DIR = Path(__file__).resolve().parent
_TEMPLATE_DIR = _BACKEND_DIR / 'templates'


class FirmwareError(ValueError):
    """A linker profile the firmware backend refuses to write (a region with
    ``origin:`` that is invalid, or does not fit below 4 GiB)."""


class SoCFirmwareBackend:
    """Generates firmware configuration files from the SoC model."""

    def __init__(self, top_module: Module,
                 config_overrides: Optional[Dict[str, Any]] = None):
        self.top = top_module
        self._stitched_map = None  # Lazily built
        # `--config-override NAME=val`: the same dict the chip and the config
        # package get, so the linker MEMORY, nanosoc_memmap.h and the RTL all
        # follow one value. Only parameters the top module declares.
        self.config_overrides = {k: v for k, v in (config_overrides or {}).items()
                                 if k in top_module.params}

    def _top_params(self) -> Dict[str, Any]:
        """The top module's parameter values, with --config-override applied."""
        params = dict(self.top.flat_params)
        params.update(self.config_overrides)
        return params

    def check_linker(self) -> None:
        """Build the linker MEMORY context and write nothing: raises
        FirmwareError for a profile generate() would refuse. __main__ runs it
        before any backend writes, so a bad ``origin:`` leaves build_soc/ as it
        was. Its console output is discarded (generate() prints it again)."""
        fw = self._find_firmware()
        if fw is None:
            return
        with contextlib.redirect_stdout(io.StringIO()):
            stitched = self._build_stitched_map()
            init_data = self._build_cpu_initiator_data(stitched, fw.cpu_initiator or 'cpu_0')
            memory_regions = self._derive_memory_regions(stitched)
            target_bases = {t['name']: t['base'] for t in stitched['targets']}
            self._build_linker_context(fw, init_data, memory_regions, target_bases)

    def generate(self, output_dir: Path):
        """Generate all firmware config files into output_dir."""
        fw = self._find_firmware()
        if fw is None:
            print("  No firmware: section found, skipping firmware config generation")
            return

        output_dir.mkdir(parents=True, exist_ok=True)

        # Build the stitched address map (all interconnects merged hierarchically)
        stitched = self._build_stitched_map()

        # Build initiator address data for the CPU initiator
        cpu_init = fw.cpu_initiator or 'cpu_0'
        init_data = self._build_cpu_initiator_data(stitched, cpu_init)

        # Derive memory regions from targets
        memory_regions = self._derive_memory_regions(stitched)

        # Build target base lookup from stitched targets
        target_bases = {t['name']: t['base'] for t in stitched['targets']}

        # Build all contexts
        linker_ctx = self._build_linker_context(fw, init_data, memory_regions, target_bases)
        memmap_ctx = self._build_memmap_context(fw, init_data, memory_regions, target_bases)
        make_ctx = self._build_makefile_context(fw, init_data, memory_regions, linker_ctx, target_bases)
        adp_ctx = self._build_adp_context(fw, init_data, target_bases)
        # CMake context = makefile context + top_name (used to resolve LD fragment paths).
        # 'cpu' already comes from make_ctx.
        cmake_ctx = dict(make_ctx)
        cmake_ctx['top_name'] = self.top.name

        if Environment is None:
            print("  WARNING: Jinja2 not available, skipping firmware config generation")
            return

        env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            keep_trailing_newline=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )

        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        source = f'soc_model ({self.top.name})'

        # Linker scripts
        template = env.get_template('firmware_memory.ld.j2')
        for profile_name, regions in linker_ctx.items():
            path = output_dir / f"{self.top.name}_{profile_name}_memory.ld"
            content = template.render(
                profile_name=profile_name, regions=regions,
                source_yaml=source, generated_date=now)
            if write_if_changed(path, content):
                print(f"  Generated: {path}")
            else:
                print(f"  Skipped (unchanged): {path}")

        # C header
        template = env.get_template('firmware_memmap.h.j2')
        path = output_dir / f"{self.top.name}_memmap.h"
        content = template.render(**memmap_ctx, source_yaml=source, generated_date=now)
        if write_if_changed(path, content):
            print(f"  Generated: {path}")
        else:
            print(f"  Skipped (unchanged): {path}")

        # Makefile
        template = env.get_template('firmware_memmap.mk.j2')
        path = output_dir / f"{self.top.name}_memmap.mk"
        content = template.render(**make_ctx, source_yaml=source, generated_date=now)
        if write_if_changed(path, content):
            print(f"  Generated: {path}")
        else:
            print(f"  Skipped (unchanged): {path}")

        # CMake (parallel to the Makefile output; consumed by NanoSoCFirmware
        # CMake package when present, ignored by the Make flow)
        template = env.get_template('firmware_memmap.cmake.j2')
        path = output_dir / f"{self.top.name}_memmap.cmake"
        content = template.render(**cmake_ctx, source_yaml=source, generated_date=now)
        if write_if_changed(path, content):
            print(f"  Generated: {path}")
        else:
            print(f"  Skipped (unchanged): {path}")

        # ADP Verilog header
        template = env.get_template('firmware_adp.vh.j2')
        path = output_dir / f"{self.top.name}_adp.vh"
        content = template.render(**adp_ctx, source_yaml=source, generated_date=now)
        if write_if_changed(path, content):
            print(f"  Generated: {path}")
        else:
            print(f"  Skipped (unchanged): {path}")

        # ADP Python module
        template = env.get_template('firmware_adp.py.j2')
        path = output_dir / f"{self.top.name}_adp.py"
        content = template.render(**adp_ctx, source_yaml=source, generated_date=now)
        if write_if_changed(path, content):
            print(f"  Generated: {path}")
        else:
            print(f"  Skipped (unchanged): {path}")

    # -----------------------------------------------------------------------
    # Firmware section lookup
    # -----------------------------------------------------------------------

    def _find_firmware(self) -> Optional[Firmware]:
        """Find the firmware config, searching top module then children."""
        if self.top.firmware:
            return self.top.firmware
        for inst in self.top.instances:
            if inst.resolved_module and inst.resolved_module.firmware:
                return inst.resolved_module.firmware
        return None

    # -----------------------------------------------------------------------
    # Stitched address map
    # -----------------------------------------------------------------------

    def _build_stitched_map(self) -> dict:
        """Build a stitched address map combining all interconnect hierarchies.

        Returns a dict with:
          targets: list of {name, base, size, phys_size, sw_access, region_type, ...}
                   where base is the absolute address from the CPU's perspective
          initiator_data: per-initiator {connections, address_regions, remap_regions}
        """
        params = self._top_params()
        all_targets = {}  # name -> {base, size, ...}
        all_init_data = {}  # init_name -> {connections, address_regions, remap_regions}

        self._stitch_module(self.top, 0, params, all_targets, all_init_data)

        return {
            'targets': list(all_targets.values()),
            'initiator_data': all_init_data,
        }

    def _stitch_module(self, module: Module, base_offset: int,
                       parent_params: Dict[str, Any],
                       all_targets: dict, all_init_data: dict):
        """Recursively stitch interconnects from module hierarchy."""
        params = dict(parent_params)
        for pname, p in module.params.items():
            # A value already in parent_params is an explicit instance override
            # passed down from the parent (see the inst.params overlay in the
            # child-recursion below). The module's own default must NOT clobber
            # it, otherwise e.g. an eth_ss with default IMEM_RAM_ADDR_W=16 would
            # ignore the parent's IMEM_RAM_ADDR_W=$ETH_IMEM_RAM_ADDR_W=15.
            if pname in parent_params:
                continue
            if p.default is not None:
                resolved = resolve_param_ref(p.default, params)
                params[pname] = resolved if resolved != p.default else p.default

        for ic in module.interconnects:
            ic_params = {}
            for k, v in ic.params.items():
                ic_params[k] = resolve_param_ref(v, params)

            # Build per-initiator data for this interconnect
            from .ahb import SoCAhbBackend
            dummy = SoCAhbBackend.__new__(SoCAhbBackend)
            dummy.top = self.top
            ic_init_data = dummy._build_initiator_data(ic)

            # Apply base offset to address regions and remap regions
            for init_name, idata in ic_init_data.items():
                offset_idata = {
                    'name': idata['name'],
                    'connections': idata['connections'],
                    'address_regions': [],
                    'remap_regions': [],
                }
                for r in idata['address_regions']:
                    offset_idata['address_regions'].append({
                        'interface': r['interface'],
                        'mem_lo': f"{int(r['mem_lo'], 16) + base_offset:08x}",
                        'mem_hi': f"{int(r['mem_hi'], 16) + base_offset:08x}",
                        'remapping': r['remapping'],
                    })
                for r in idata['remap_regions']:
                    offset_idata['remap_regions'].append({
                        'interface': r['interface'],
                        'mem_lo': f"{int(r['mem_lo'], 16) + base_offset:08x}",
                        'mem_hi': f"{int(r['mem_hi'], 16) + base_offset:08x}",
                        'bit': r['bit'],
                    })

                # Merge into all_init_data (combine if same initiator name)
                if init_name in all_init_data:
                    existing = all_init_data[init_name]
                    existing['connections'].extend(offset_idata['connections'])
                    existing['address_regions'].extend(offset_idata['address_regions'])
                    existing['remap_regions'].extend(offset_idata['remap_regions'])
                else:
                    all_init_data[init_name] = offset_idata

            # Add targets with absolute addresses
            for t in ic.targets:
                abs_base = t.base + base_offset
                phys_size = t.phys_size
                if isinstance(phys_size, str):
                    phys_size = resolve_param_ref(phys_size, params)
                    if isinstance(phys_size, (int, float)):
                        phys_size = int(phys_size)
                    else:
                        phys_size = None

                if t.name not in all_targets:
                    all_targets[t.name] = {
                        'name': t.name,
                        'base': abs_base,
                        'size': t.size,
                        'phys_size': phys_size,
                        'sw_access': t.sw_access,
                        'region_type': t.region_type,
                        'role': t.role,
                        'linker_name': t.name.upper(),
                    }

                # Recurse into child instances for this target
                if t.instance:
                    inst = module.get_instance(t.instance)
                    if inst and inst.resolved_module and inst.resolved_module.interconnects:
                        # Overlay the instance's parameter overrides (resolved
                        # against the parent's params) so the child module sees
                        # the values the parent actually instantiates it with.
                        # Without this, region phys_size expressions like
                        # "2 ** $IMEM_RAM_ADDR_W" resolve against the child
                        # module's own default (e.g. eth_ss default 16 = 64 KB)
                        # instead of the parent override (e.g.
                        # IMEM_RAM_ADDR_W: $ETH_IMEM_RAM_ADDR_W = 15 = 32 KB),
                        # so the generated linker MEMORY LENGTH would ignore the
                        # configured IMEM size.
                        child_params = dict(params)
                        for pname, pval in inst.params.items():
                            child_params[pname] = resolve_param_ref(pval, params)
                        self._stitch_module(
                            inst.resolved_module, abs_base, child_params,
                            all_targets, all_init_data
                        )

            # -----------------------------------------------------------
            # Bridge-merge: a parent-IC initiator whose `instance` is a
            # subsystem boundary receives traffic from a child-IC initiator
            # that exits the subsystem via a passthrough target. The child
            # initiator carries the subsystem's internal address + remap
            # metadata (IMEM remapped window, BOOTROM alias, ...). Fold
            # those regions into the parent initiator's view so linker
            # profiles that use the parent initiator name (e.g. eth_ss_m)
            # resolve `address_select: alias|remapped` correctly without
            # reaching into the sub-initiator.
            #
            # Skip the passthrough target's own region — its address window
            # routes back out to parent-IC targets which are already in the
            # parent initiator's map, so including it would double-count.
            # -----------------------------------------------------------
            for init in ic.initiators:
                if not init.instance:
                    continue
                parent_entry = all_init_data.get(init.name)
                if parent_entry is None:
                    continue
                inst = module.get_instance(init.instance)
                if not (inst and inst.resolved_module):
                    continue
                needle = f"{ic.name}.{init.name}"
                bridge_port = next(
                    (c.port for c in inst.connections if c.conn == needle),
                    None,
                )
                if bridge_port is None:
                    continue
                for child_ic in inst.resolved_module.interconnects:
                    exit_targets = {
                        t.name for t in child_ic.targets
                        if t.passthrough == bridge_port
                    }
                    if not exit_targets:
                        continue
                    # De-dupe keys so multiple internal masters that overlap
                    # (e.g. a CPU + a DMA that both reach the same memories)
                    # contribute each region only once.
                    seen_addr = {
                        (r['interface'], r['mem_lo'], r['mem_hi'], r['remapping'])
                        for r in parent_entry['address_regions']
                    }
                    seen_remap = {
                        (r['interface'], r['mem_lo'], r['mem_hi'], r['bit'])
                        for r in parent_entry['remap_regions']
                    }
                    for child_init in child_ic.initiators:
                        if child_init.name == init.name:
                            continue  # same-name merge already handled
                        # Skip passthrough initiators — those are external
                        # slave-ins (parent drives into them), not internal
                        # masters feeding the parent bridge initiator.
                        if child_init.passthrough:
                            continue
                        if not (exit_targets & {t.name for t in child_init.targets}):
                            continue
                        child_entry = all_init_data.get(child_init.name)
                        if child_entry is None:
                            continue
                        for r in child_entry['address_regions']:
                            if r['interface'] in exit_targets:
                                continue
                            key = (r['interface'], r['mem_lo'], r['mem_hi'], r['remapping'])
                            if key in seen_addr:
                                continue
                            seen_addr.add(key)
                            parent_entry['address_regions'].append(dict(r))
                        for r in child_entry['remap_regions']:
                            if r['interface'] in exit_targets:
                                continue
                            key = (r['interface'], r['mem_lo'], r['mem_hi'], r['bit'])
                            if key in seen_remap:
                                continue
                            seen_remap.add(key)
                            parent_entry['remap_regions'].append(dict(r))

    # -----------------------------------------------------------------------
    # CPU initiator data
    # -----------------------------------------------------------------------

    def _build_cpu_initiator_data(self, stitched: dict, cpu_init: str) -> dict:
        """Get the stitched initiator data for the CPU."""
        init_data = stitched['initiator_data'].get(cpu_init)
        if init_data is None:
            available = list(stitched['initiator_data'].keys())
            raise ValueError(
                f"CPU initiator '{cpu_init}' not found in stitched address map. "
                f"Available: {available}"
            )
        return init_data

    # -----------------------------------------------------------------------
    # Memory region helpers
    # -----------------------------------------------------------------------

    def _derive_memory_regions(self, stitched: dict) -> List[dict]:
        """Extract memory region metadata from stitched targets."""
        regions = []
        for t in stitched['targets']:
            rt = t.get('region_type')
            if rt not in ('memory', 'periph', 'peripheral'):
                continue
            # Normalize region_type
            if rt == 'periph':
                rt = 'peripheral'
            regions.append({
                'target': t['name'],
                'region_type': rt,
                'linker_name': t.get('linker_name', t['name'].upper()),
                'software_access': t.get('sw_access', 'rwx'),
                'phys_size': t.get('phys_size'),
                'role': t.get('role'),
            })
        return regions

    def _find_memory_region(self, regions: List[dict], target_name: str) -> Optional[dict]:
        """Find a memory region by target name."""
        for mr in regions:
            if mr['target'] == target_name:
                return mr
        return None

    # -----------------------------------------------------------------------
    # Address resolution
    # -----------------------------------------------------------------------

    def _resolve_address(self, target_name: str, address_select: str,
                         init_data: dict,
                         target_bases: Optional[Dict[str, int]] = None) -> int:
        """Resolve a target's base address given an address selection policy.

        For 'default', uses the canonical base from the interconnect target definition
        (via target_bases lookup). For 'alias' and 'remapped', uses the initiator's
        address map to find alternative address windows.
        """
        if address_select == 'default' and target_bases and target_name in target_bases:
            return target_bases[target_name]

        if address_select == 'remapped':
            regions = self._compute_effective_map(init_data, {0: True})
            for r in regions:
                if r['interface'] == target_name and r['source'] == 'remap_region':
                    return int(r['mem_lo'], 16)
            # Fallback: remap overlay at 0x00000000 (common for instruction memory)
            for r in regions:
                if r['interface'] == target_name:
                    return int(r['mem_lo'], 16)
            raise ValueError(f"Cannot resolve remapped address for target '{target_name}'")

        # For alias, use no-remap map
        regions = self._compute_effective_map(init_data, {})
        target_regions = [r for r in regions if r['interface'] == target_name]

        if not target_regions:
            raise ValueError(f"Target '{target_name}' not found in address map")

        if address_select == 'alias':
            # Try region explicitly marked as alias
            for r in target_regions:
                if r.get('remapping') == 'alias':
                    return int(r['mem_lo'], 16)
            # Try the second address window
            if len(target_regions) >= 2:
                return int(target_regions[1]['mem_lo'], 16)
            raise ValueError(f"Cannot resolve alias address for target '{target_name}'")

        # Fallback for default without target_bases: lowest canonical entry
        for r in target_regions:
            if (r['source'] == 'address_region'
                    and r.get('remapping') != 'alias'):
                return int(r['mem_lo'], 16)

        return int(target_regions[0]['mem_lo'], 16)

    def _compute_effective_map(self, init_data: dict,
                               remap_config: Dict[int, bool]) -> List[dict]:
        """Compute effective address map (reuse AHB backend logic)."""
        from .ahb import SoCAhbBackend
        dummy = SoCAhbBackend.__new__(SoCAhbBackend)
        dummy.top = self.top
        return dummy._compute_effective_address_map(init_data, remap_config)

    # -----------------------------------------------------------------------
    # Context builders
    # -----------------------------------------------------------------------

    def _build_linker_context(self, fw: Firmware, init_data: dict,
                              memory_regions: List[dict],
                              target_bases: Dict[str, int]) -> Dict[str, list]:
        """Build linker MEMORY block context for each linker_profile."""
        profiles = {}
        for profile in fw.linker_profiles:
            regions = []
            for idx, lr in enumerate(profile.regions):
                mr = self._find_memory_region(memory_regions, lr.target)
                if mr is None:
                    print(f"  WARNING: target '{lr.target}' has no region_type, skipping")
                    continue

                where = (f"firmware: linker profile '{profile.name}' region {idx} "
                         f"(target '{lr.target}')")
                if lr.origin is not None:
                    try:
                        origin = linker_origin(lr.origin, where)
                    except ValueError as e:
                        raise FirmwareError(str(e)) from None
                else:
                    origin = self._resolve_address(lr.target, lr.address_select, init_data, target_bases)

                phys_size = lr.phys_size
                if phys_size is None:
                    phys_size = mr.get('phys_size')
                if phys_size is None:
                    print(f"  WARNING: no phys_size for target '{lr.target}' in "
                          f"profile '{profile.name}', skipping")
                    continue
                if isinstance(phys_size, str):
                    phys_size = int(phys_size, 0)

                size_adjust = lr.size_adjust or 0
                length = phys_size + size_adjust
                # A MEMORY region must have a positive LENGTH. Without this, a
                # `phys_size: 0` wrote `LENGTH = 0x0`, and a size_adjust larger
                # than the size wrote `LENGTH = 0x-300`, which no linker parses
                # (review R-TRAIN5). Every region, with or without `origin:`.
                if length <= 0:
                    src = ("its phys_size" if lr.phys_size is not None
                           else f"target '{lr.target}' phys_size")
                    raise FirmwareError(
                        f"{where}: LENGTH would be {length} ({src} {phys_size:#x}"
                        f"{f' + size_adjust {size_adjust}' if size_adjust else ''}); a "
                        f"linker region must be at least 1 byte")
                if lr.origin is not None and origin + length > 1 << 32:
                    raise FirmwareError(
                        f"{where}: origin 0x{origin:08X} + length 0x{length:X} ends at "
                        f"0x{origin + length:X}, past the 32-bit address space")

                linker_name = lr.linker_name or mr.get('linker_name', lr.target.upper())
                attrs = lr.software_access or mr.get('software_access', 'rwx')

                size_kb = length // 1024
                if size_kb >= 1024:
                    comment = f"{size_kb // 1024}MB"
                elif size_kb > 0:
                    comment = f"{size_kb}K"
                else:
                    comment = f"{length} bytes"

                regions.append({
                    'name': linker_name,
                    'attrs': attrs,
                    'origin': f"0x{origin:08X}",
                    'length': f"0x{length:X}",
                    'comment': comment,
                })
            profiles[profile.name] = regions
        return profiles

    def _build_memmap_context(self, fw: Firmware, init_data: dict,
                              memory_regions: List[dict],
                              target_bases: Dict[str, int]) -> dict:
        """Build C header context with base address defines."""
        memory_defines = []
        for mr in memory_regions:
            target = mr['target']
            role = mr.get('role') or mr['region_type']
            linker_name = mr['linker_name']
            rt = mr['region_type']

            try:
                default_addr = self._resolve_address(target, 'default', init_data, target_bases)
            except ValueError:
                continue

            prefix = self.top.name.upper()
            memory_defines.append({
                'define_name': f"{prefix}_{linker_name}_BASE",
                'base': f"0x{default_addr:08X}",
                'comment': f"{linker_name} ({role})",
            })

            if rt == 'memory':
                phys_size = mr.get('phys_size')
                if phys_size is not None:
                    memory_defines.append({
                        'define_name': f"{prefix}_{linker_name}_SIZE",
                        'base': f"0x{phys_size:08X}",
                        'comment': f"{linker_name} physical size",
                    })

                try:
                    alias_addr = self._resolve_address(target, 'alias', init_data, target_bases)
                    if alias_addr != default_addr:
                        memory_defines.append({
                            'define_name': f"{prefix}_{linker_name}_ALIAS",
                            'base': f"0x{alias_addr:08X}",
                            'comment': f"{linker_name} alias",
                        })
                except ValueError:
                    pass

                try:
                    remap_addr = self._resolve_address(target, 'remapped', init_data, target_bases)
                    if remap_addr != default_addr:
                        memory_defines.append({
                            'define_name': f"{prefix}_{linker_name}_REMAP",
                            'base': f"0x{remap_addr:08X}",
                            'comment': f"{linker_name} remapped",
                        })
                except ValueError:
                    pass

        # Build config params from top-level parameters that firmware needs
        config_params = []
        flat_params = flatten_params({n: p.default for n, p in self.top.params.items()})
        flat_params.update(self.config_overrides)

        # Export firmware-relevant parameters
        _FW_PARAMS = {
            'SYS_CLK_FREQ_HZ': 'System clock frequency in Hz',
            'NUMIRQ':           'Number of IRQ lines',
        }
        # Parameters a BUILD may override with -D<define>=<value>, so the header
        # emits them inside #ifndef. The system clock is the one that differs by
        # target: the YAML states the clock the SoC was designed for, and an FPGA
        # board runs the same SoC at whatever its clocking gives it (25 MHz on
        # PYNQ-Z2 and KR260, 50 MHz on MPS3). Every baud divisor and timer the
        # firmware computes comes from this macro, so without an override the
        # console runs at the wrong rate and nothing fails. NUMIRQ describes the
        # hardware itself and stays fixed.
        _FW_OVERRIDABLE = {'SYS_CLK_FREQ_HZ'}
        prefix = self.top.name.upper()
        for pname, comment in _FW_PARAMS.items():
            if pname in flat_params:
                val = flat_params[pname]
                config_params.append({
                    'define_name': f"{prefix}_{pname}",
                    'value': f"{val}UL" if isinstance(val, int) else str(val),
                    'comment': comment,
                    'overridable': pname in _FW_OVERRIDABLE,
                })

        return {
            'guard_name': f"{self.top.name.upper()}_MEMMAP_GENERATED_H",
            'memory_regions': memory_defines,
            'config_params': config_params,
        }

    def _build_makefile_context(self, fw: Firmware, init_data: dict,
                                memory_regions: List[dict],
                                linker_ctx: Dict[str, list],
                                target_bases: Dict[str, int]) -> dict:
        """Build Makefile include context."""
        region_vars = []
        for mr in memory_regions:
            target = mr['target']
            linker_name = mr['linker_name']
            try:
                default_addr = self._resolve_address(target, 'default', init_data, target_bases)
            except ValueError:
                continue
            phys_size = mr.get('phys_size') if mr['region_type'] == 'memory' else None
            region_vars.append({
                'var_name': linker_name,
                'base': f"0x{default_addr:08X}",
                'size': f"0x{phys_size:X}" if phys_size else "0x0",
            })

        profile_vars = {}
        for pname, regions in linker_ctx.items():
            ro_base = "0x00000000"
            rw_base = "0x00000000"
            for r in regions:
                if 'r' in r['attrs'] and 'w' not in r['attrs']:
                    ro_base = r['origin']
                elif 'w' in r['attrs']:
                    rw_base = r['origin']
            profile_vars[pname] = {'ro_base': ro_base, 'rw_base': rw_base}

        hex_adjust = {}
        if fw.hex_adjust:
            for adj_name, adj_cfg in fw.hex_adjust.items():
                target = adj_cfg['target']
                addr_select = adj_cfg.get('address_select', 'default')
                try:
                    addr = self._resolve_address(target, addr_select, init_data, target_bases)
                    hex_adjust[adj_name] = f"-0x{addr:08X}"
                except ValueError:
                    pass

        adp_cfg = fw.adp or {}
        adp_addr = "0x00000000"
        if adp_cfg:
            target = adp_cfg['upload_target']
            addr_select = adp_cfg.get('address_select', 'default')
            try:
                addr = self._resolve_address(target, addr_select, init_data, target_bases)
                adp_addr = f"0x{addr:08X}"
            except ValueError:
                pass

        return {
            'region_vars': region_vars,
            'profile_vars': profile_vars,
            'hex_adjust': hex_adjust,
            'adp_upload_address': adp_addr,
            'cpu': fw.cpu,
        }

    def _build_adp_context(self, fw: Firmware, init_data: dict,
                           target_bases: Dict[str, int] = None) -> dict:
        """Build ADP context for Verilog and Python outputs."""
        adp_cfg = fw.adp or {}
        if not adp_cfg:
            return {
                'adp_address_hex': '00000000',
                'adp_nibbles': [],
                'adp_upload_address_python': '0x00000000',
            }

        target = adp_cfg['upload_target']
        addr_select = adp_cfg.get('address_select', 'default')
        try:
            addr = self._resolve_address(target, addr_select, init_data, target_bases)
        except ValueError:
            addr = 0

        hex_str = f"{addr:08X}"
        nibbles = [(i, ch) for i, ch in enumerate(hex_str)]

        return {
            'adp_address_hex': hex_str.lower(),
            'adp_nibbles': nibbles,
            'adp_upload_address_python': f"0x{hex_str}",
        }
