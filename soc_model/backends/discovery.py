"""Discovery table backend — generates a register map describing the bus topology.

For each interconnect with gen: True, produces a read-only RegisterMap that
encodes the target (slave) address map and initiator (master) visibility.
Firmware or tools can read these registers at runtime to discover which
devices exist, their addresses, and which bus masters can access them.

Generated register layout (all 32-bit, read-only):

  Header:
    0x000  TABLE_ID            — magic 0x534F4344 ("SOCD")
    0x004  TABLE_VERSION       — [15:0] = format version (1)
    0x008  TABLE_SIZE          — [7:0] = num_targets, [15:8] = num_initiators,
                                 [31:16] = address width
    0x00C  INTERCONNECT_NAME   — 4-char packed ASCII (little-endian)

  Per-target (4 regs each, starting at 0x010):
    +0x00  TGT_i_BASE          — base address
    +0x04  TGT_i_SIZE          — address window size
    +0x08  TGT_i_ATTR          — [3:0] region_type, [7:4] sw_access,
                                 [15:8] protocol, [23:16] target_id
    +0x0C  TGT_i_NAME          — 4-char packed ASCII

  Per-initiator (2 regs each, after targets):
    +0x00  INIT_j_NAME          — 4-char packed ASCII
    +0x04  INIT_j_VISIBILITY    — bitmask (bit N = can access target N)

Output files per interconnect:
  - build_soc/discovery/<ic_name>_discovery.yaml   — YAML register map
"""

import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from ..model import (
    Interconnect, InterconnectInitiator, InterconnectTarget,
    Module, Register, RegisterField, RegisterMap,
)
from ..utils import resolve_param_ref, write_if_changed


# Magic number: ASCII "SOCD" in little-endian
_TABLE_MAGIC = 0x534F4344

# Current format version
_TABLE_VERSION = 1

# Enum mappings
_REGION_TYPE_MAP = {
    'memory': 1,
    'periph': 2,
    'peripheral': 2,
    'debug': 3,
}

_SW_ACCESS_MAP = {
    'none': 0,
    'ro': 1,
    'wo': 2,
    'rw': 3,
    'rwx': 4,
}

_PROTOCOL_MAP = {
    'ahb': 0,
    'apb': 1,
}


def _encode_name(name: str) -> int:
    """Pack the first 4 characters of a name as a little-endian 32-bit integer."""
    padded = (name[:4] + '\x00\x00\x00\x00')[:4]
    return sum(ord(c) << (8 * i) for i, c in enumerate(padded))


def _encode_region_type(rt: Optional[str]) -> int:
    if rt is None:
        return 0
    return _REGION_TYPE_MAP.get(rt.lower(), 0)


def _encode_sw_access(access: Optional[str]) -> int:
    if access is None:
        return 0
    return _SW_ACCESS_MAP.get(access.lower(), 0)


def _encode_protocol(proto: Optional[str]) -> int:
    if proto is None:
        return 0
    return _PROTOCOL_MAP.get(proto.lower(), 0)


def _build_visibility_mask(initiator: InterconnectInitiator,
                           target_names: List[str]) -> int:
    """Build a bitmask indicating which targets this initiator can access."""
    mask = 0
    for tgt_ref in initiator.targets:
        if tgt_ref.name in target_names:
            bit = target_names.index(tgt_ref.name)
            mask |= (1 << bit)
    return mask


class SoCDiscoveryBackend:
    """Generates device discovery table register maps from the SoC model."""

    def __init__(self, top_module: Module,
                 config_overrides: Optional[Dict[str, Any]] = None):
        self.top = top_module
        # `--config-override`: as the chip, the config package and the firmware
        # backend take it, so a discovery table reports the sizes the RTL has.
        self.config_overrides = {k: v for k, v in (config_overrides or {}).items()
                                 if k in top_module.params}

    def generate(self, build_dir: Path) -> List[Tuple[str, RegisterMap, Path]]:
        """Generate discovery tables for all interconnects with gen: True.

        Returns list of (ic_name, register_map, yaml_path) tuples.
        """
        results = []
        self._generate_for_module(self.top, {}, build_dir, results,
                                  overrides=dict(self.config_overrides))

        return results

    def _generate_for_module(self, module: Module, parent_params: Dict[str, Any],
                             build_dir: Path,
                             results: List[Tuple[str, RegisterMap, Path]],
                             overrides: Optional[Dict[str, Any]] = None):
        """Recursively find interconnects and generate discovery tables.

        `overrides` are the values the parent instantiates this module with
        (its instance `params:`, resolved in the parent's scope). They win
        over the module's own defaults, exactly as in the RTL. Without them a
        region's "2 ** $IMEM_RAM_ADDR_W" resolved against the SUBSYSTEM's
        default (16 KB) while the top set 128 KB, and the table reported a
        memory size the hardware does not have.
        """
        overrides = overrides or {}
        params = dict(parent_params)
        params.update(overrides)
        for pname, p in module.params.items():
            if pname in overrides:
                continue
            if p.default is not None:
                resolved = resolve_param_ref(p.default, params)
                params[pname] = resolved if resolved != p.default else p.default

        for ic in module.interconnects:
            if not ic.gen:
                continue

            rm = self.build_register_map(ic, params)
            yaml_path = self._write_yaml(rm, ic, build_dir)
            results.append((ic.name, rm, yaml_path))

        # Recurse into child instances, with the values each is instantiated with
        for inst in module.instances:
            if inst.resolved_module:
                child_over = {}
                for pname, pval in (inst.params or {}).items():
                    v = pval
                    for _ in range(16):        # "$A" -> "$B" chains
                        nxt = resolve_param_ref(v, params)
                        if nxt == v:
                            break
                        v = nxt
                    child_over[pname] = v
                self._generate_for_module(inst.resolved_module, params,
                                          build_dir, results, overrides=child_over)

    def build_register_map(self, ic: Interconnect,
                           params: Dict[str, Any]) -> RegisterMap:
        """Build a RegisterMap encoding the interconnect's bus topology."""
        targets = ic.targets
        initiators = ic.initiators
        target_names = [t.name for t in targets]

        # Resolve address width from interconnect params
        addr_width = 32
        for key in ('SYS_ADDR_W', 'ADDR_W', 'routing_address_width'):
            val = ic.params.get(key)
            if val is not None:
                resolved = resolve_param_ref(val, params)
                if isinstance(resolved, int):
                    addr_width = resolved
                    break

        registers: List[Register] = []
        offset = 0x000

        # --- Header registers ---
        registers.append(Register(
            name='TABLE_ID',
            offset=offset,
            width=32,
            access='RO',
            reset_value=_TABLE_MAGIC,
            desc='Magic number 0x534F4344 ("SOCD") identifying a discovery table',
        ))
        offset += 4

        registers.append(Register(
            name='TABLE_VERSION',
            offset=offset,
            width=32,
            access='RO',
            reset_value=_TABLE_VERSION,
            desc='Discovery table format version',
            fields=[
                RegisterField(name='VERSION', bits='15:0', access='RO',
                              reset_value=_TABLE_VERSION,
                              desc='Format version number'),
                RegisterField(name='RESERVED', bits='31:16', access='RO',
                              reset_value=0, desc='Reserved for future use'),
            ],
        ))
        offset += 4

        table_size_val = (
            (len(targets) & 0xFF) |
            ((len(initiators) & 0xFF) << 8) |
            ((addr_width & 0xFFFF) << 16)
        )
        registers.append(Register(
            name='TABLE_SIZE',
            offset=offset,
            width=32,
            access='RO',
            reset_value=table_size_val,
            desc='Number of targets, initiators, and address width',
            fields=[
                RegisterField(name='NUM_TARGETS', bits='7:0', access='RO',
                              reset_value=len(targets),
                              desc='Number of target descriptors'),
                RegisterField(name='NUM_INITIATORS', bits='15:8', access='RO',
                              reset_value=len(initiators),
                              desc='Number of initiator descriptors'),
                RegisterField(name='ADDR_WIDTH', bits='31:16', access='RO',
                              reset_value=addr_width,
                              desc='System address width in bits'),
            ],
        ))
        offset += 4

        registers.append(Register(
            name='INTERCONNECT_NAME',
            offset=offset,
            width=32,
            access='RO',
            reset_value=_encode_name(ic.name),
            desc=f'Interconnect short name (4-char packed ASCII): "{ic.name[:4]}"',
        ))
        offset += 4

        # --- Target descriptors (4 registers each) ---
        for i, tgt in enumerate(targets):
            # Resolve physical size if it's a parameter expression
            size_val = tgt.size
            if isinstance(tgt.phys_size, str):
                resolved = resolve_param_ref(tgt.phys_size, params)
                if isinstance(resolved, (int, float)):
                    size_val = int(resolved)
            elif isinstance(tgt.phys_size, int):
                size_val = tgt.phys_size

            registers.append(Register(
                name=f'TGT_{i}_BASE',
                offset=offset,
                width=32,
                access='RO',
                reset_value=tgt.base,
                desc=f'Target {i} ({tgt.name}) base address',
            ))
            offset += 4

            registers.append(Register(
                name=f'TGT_{i}_SIZE',
                offset=offset,
                width=32,
                access='RO',
                reset_value=size_val,
                desc=f'Target {i} ({tgt.name}) address window size',
            ))
            offset += 4

            attr_val = (
                (_encode_region_type(tgt.region_type) & 0xF) |
                ((_encode_sw_access(tgt.sw_access) & 0xF) << 4) |
                ((_encode_protocol(tgt.protocol) & 0xFF) << 8) |
                ((i & 0xFF) << 16)
            )
            registers.append(Register(
                name=f'TGT_{i}_ATTR',
                offset=offset,
                width=32,
                access='RO',
                reset_value=attr_val,
                desc=f'Target {i} ({tgt.name}) attributes',
                fields=[
                    RegisterField(name='REGION_TYPE', bits='3:0', access='RO',
                                  reset_value=_encode_region_type(tgt.region_type),
                                  desc='Region type (0=unknown, 1=memory, 2=periph, 3=debug)'),
                    RegisterField(name='SW_ACCESS', bits='7:4', access='RO',
                                  reset_value=_encode_sw_access(tgt.sw_access),
                                  desc='Software access (0=none, 1=ro, 2=wo, 3=rw, 4=rwx)'),
                    RegisterField(name='PROTOCOL', bits='15:8', access='RO',
                                  reset_value=_encode_protocol(tgt.protocol),
                                  desc='Bus protocol (0=ahb, 1=apb)'),
                    RegisterField(name='TARGET_ID', bits='23:16', access='RO',
                                  reset_value=i,
                                  desc='Unique target index'),
                ],
            ))
            offset += 4

            registers.append(Register(
                name=f'TGT_{i}_NAME',
                offset=offset,
                width=32,
                access='RO',
                reset_value=_encode_name(tgt.name),
                desc=f'Target {i} short name (4-char packed ASCII): "{tgt.name[:4]}"',
            ))
            offset += 4

        # --- Initiator descriptors (2 registers each) ---
        for j, init in enumerate(initiators):
            vis_mask = _build_visibility_mask(init, target_names)

            registers.append(Register(
                name=f'INIT_{j}_NAME',
                offset=offset,
                width=32,
                access='RO',
                reset_value=_encode_name(init.name),
                desc=f'Initiator {j} short name (4-char packed ASCII): "{init.name[:4]}"',
            ))
            offset += 4

            registers.append(Register(
                name=f'INIT_{j}_VISIBILITY',
                offset=offset,
                width=32,
                access='RO',
                reset_value=vis_mask,
                desc=f'Initiator {j} ({init.name}) target visibility bitmask',
            ))
            offset += 4

        rm_name = f'{ic.name}_discovery'
        return RegisterMap(
            name=rm_name,
            module=f'{ic.name}_discovery_table',
            gen=True,
            desc=(f'Auto-generated device discovery table for {ic.name}. '
                  f'Read-only registers encoding {len(targets)} targets and '
                  f'{len(initiators)} initiators with address maps and visibility.'),
            address_width=12,
            data_width=32,
            registers=registers,
            source_file='(generated by soc_model discovery backend)',
        )

    def _write_yaml(self, rm: RegisterMap, ic: Interconnect,
                    build_dir: Path) -> Path:
        """Write the register map as a YAML file for documentation."""
        disc_dir = build_dir / 'discovery'
        disc_dir.mkdir(parents=True, exist_ok=True)

        yaml_path = disc_dir / f'{rm.name}.yaml'

        # Build YAML-compatible dict
        data = {
            'register_map': {
                'name': rm.name,
                'module': rm.module,
                'gen': rm.gen,
                'description': rm.desc,
                'address_width': rm.address_width,
                'data_width': rm.data_width,
                'registers': [],
            }
        }

        for reg in rm.registers:
            reg_dict = {
                'name': reg.name,
                'offset': f'0x{reg.offset:03X}',
                'width': reg.width,
                'access': reg.access,
                'description': reg.desc,
            }
            if reg.reset_value is not None:
                reg_dict['reset_value'] = f'0x{reg.reset_value:08X}'
            if reg.fields:
                reg_dict['fields'] = []
                for fld in reg.fields:
                    fld_dict = {
                        'name': fld.name,
                        'bits': fld.bits,
                        'access': fld.access,
                        'description': fld.desc,
                    }
                    if fld.reset_value is not None:
                        fld_dict['reset_value'] = fld.reset_value
                    reg_dict['fields'].append(fld_dict)
            data['register_map']['registers'].append(reg_dict)

        # Write with a header comment
        header = (
            f"# Auto-generated discovery table register map for {ic.name}\n"
            f"# Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"# DO NOT EDIT — regenerate with soc_model\n\n"
        )
        content = header + yaml.dump(data, default_flow_style=False,
                                     sort_keys=False, width=120)
        write_if_changed(yaml_path, content)
        return yaml_path
