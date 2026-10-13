"""Builds the OO model from parsed YAML dictionaries."""

import difflib
from typing import Any, Dict, List, Optional

from .model import (
    AddressDecode, AddressDecodeSlot, Assign, BuildInfo, BuildInfoField,
    Clock, ClockGroup, Connection, ConstraintSet, Firmware, FalsePath,
    GeneratedClock, GlueLogicEntry, Instance, Interconnect,
    InterconnectInitiator, InterconnectInitiatorTarget, InterconnectTarget,
    ChipBoundary, ChipPad, ChipTerminator, FpgaProfile, IoDelay, MulticyclePath,
    Interface, LinkerProfile, LinkerRegion, Module, PadCell, PadSignal, PadTech,
    PinMap, PowerPad, Param, Register, RegisterField, RegisterMap, Reset,
    SramEntry,
    SRAM_ADDR_UNITS,
)
from .parser import SoCParser
from .utils import linker_origin, resolve_param_ref

# The interconnect keys _build_interconnect reads, plus `auto_base`, which the
# YAML expander (expand.py) consumes before the builder when it runs.
INTERCONNECT_YAML_KEYS = frozenset({
    'name', 'gen', 'type', 'desc', 'params', 'perf_probes', 'impl',
    'arbitration_scheme',
    'connections', 'targets', 'initiators', 'auto_base',
})
# The keys _build_interconnect reads from a target, an initiator and an
# initiator's target entry. Anything else is ignored; it is recorded (see
# _record_unknown) so impl: cmsdk_mux can refuse a misspelt key.
INTERCONNECT_TARGET_YAML_KEYS = frozenset({
    'name', 'instance', 'base', 'size', 'phys_size', 'sw_access', 'region_type',
    'role', 'subordinate_bus', 'protocol', 'apb_config', 'passthrough', 'probe',
    'register_map', 'desc',
})
INTERCONNECT_INITIATOR_YAML_KEYS = frozenset({'name', 'instance', 'passthrough', 'probe', 'targets'})
INITIATOR_TARGET_YAML_KEYS = frozenset({'name', 'visibility'})
# The keys _build_firmware reads from a linker-profile region. Any other key is
# REFUSED (ValueError: the model is not built): the builder used to drop it, so
# a misspelt `orign:` linked the region at the SoC target's address in silence.
LINKER_REGION_YAML_KEYS = frozenset({
    'target', 'address_select', 'linker_name', 'software_access', 'size_adjust',
    'phys_size', 'origin',
})


def _record_unknown(obj, data, known) -> None:
    """Record ``data``'s keys that are not in ``known`` on ``obj`` as a plain
    attribute (not a dataclass field, so no model dump, report or schema doc
    changes). Only backends.cmsdk_mux reads it."""
    obj._unknown_yaml_keys = {k: v for k, v in data.items() if k not in known}


class SoCBuilder:
    """Constructs Module objects from parsed YAML data."""

    def __init__(self, parser: SoCParser):
        self.parser = parser
        self._built_modules: Dict[str, Module] = {}

    def build_system(self, filename: str) -> Module:
        """Build the complete system model from a top-level YAML file."""
        data = self.parser.parse_top_level(filename)
        if 'module' not in data:
            raise ValueError(f"No 'module:' key in {filename}")

        module = self._build_module(data['module'], filename)

        # Resolve module references for all instances
        self._resolve_instances(module)

        return module

    def build_from_dict(self, data: Dict[str, Any],
                        source_file: str = '<dict>') -> Module:
        """Build the complete system model from an in-memory dict.

        ``data`` is the raw top-level shape the parser loads — either
        ``{'module': {...}}`` or the bare module dict. Instance ``module:``
        references resolve through this builder's parser (its lib dirs),
        exactly as in :meth:`build_system`. Used by the GUI service layer,
        which edits the dict directly and builds a transient model for
        validation/diagramming. Use a fresh builder per call — built modules
        are cached by name.
        """
        if not isinstance(data, dict):
            raise ValueError("build_from_dict: expected a dict")
        mod = data.get('module', data)
        if not isinstance(mod, dict):
            raise ValueError("build_from_dict: 'module:' is not a mapping")
        module = self._build_module(mod, source_file)
        self._resolve_instances(module)
        return module

    def _build_module(self, data: Dict[str, Any], source_file: str = '') -> Module:
        """Build a Module from a YAML module dict."""
        name = data.get('name', 'unknown')

        if name in self._built_modules:
            return self._built_modules[name]

        module = Module(
            name=name,
            gen=data.get('gen', False),
            desc=data.get('desc', ''),
            source_file=source_file,
        )

        # Parse params
        for pname, pval in data.get('params', {}).items():
            if isinstance(pval, dict):
                module.params[pname] = Param(
                    name=pname,
                    type=pval.get('type', 'int'),
                    default=pval.get('default'),
                    desc=pval.get('desc', ''),
                )
            else:
                module.params[pname] = Param(name=pname, default=pval)

        # Parse clocks
        for c in data.get('clocks', []):
            module.clocks.append(Clock(
                name=c.get('name', ''),
                source=c.get('source', ''),
                desc=c.get('desc', ''),
                freq_hz=c.get('freq_hz'),
                period_ns=c.get('period_ns'),
                port=c.get('port'),
            ))

        # Parse resets
        for r in data.get('resets', []):
            module.resets.append(Reset(
                name=r.get('name', ''),
                active=r.get('active', 'low'),
                source=r.get('source', ''),
                desc=r.get('desc', ''),
            ))

        # Parse interfaces
        for iface in data.get('interfaces', []):
            module.interfaces.append(self._build_interface(iface))

        # Parse instances
        for inst in data.get('instances', []):
            module.instances.append(self._build_instance(inst))

        # Parse assigns (legacy format)
        for a in data.get('assigns', []):
            module.assigns.append(Assign(
                target=str(a.get('target', '')),
                expr=str(a.get('expr', '')),
                type=a.get('type'),
                bit=a.get('bit'),
                combining=a.get('combining'),
                origin=a.get('origin'),
                desc=a.get('desc', ''),
            ))

        # Parse internal_wires
        for w in data.get('internal_wires', []):
            module.internal_wires.append(Interface(
                name=w.get('name', ''),
                type=w.get('type', 'wire'),
                direction='internal',
                params=w.get('params', {}),
                desc=w.get('desc', ''),
            ))

        # Parse glue_logic (typed structural entries)
        for gl in data.get('glue_logic', []):
            entry = GlueLogicEntry(
                name=gl.get('name', ''),
                type=gl.get('type', ''),
                output=str(gl.get('output', '')),
                inputs=gl.get('inputs', []),
                input=gl.get('input'),
                value=gl.get('value'),
                width=gl.get('width'),
                select=gl.get('select'),
                desc=gl.get('desc', ''),
            )
            if entry.type == 'concat':
                # concat keeps its structured [{bit, signal}] inputs untouched;
                # the string normalisation below would mangle the dicts.
                entry.concat_inputs = gl.get('inputs', [])
                entry.pad_zero_above = gl.get('pad_zero_above')
                entry.inputs = []
            else:
                # Normalise: ensure inputs are strings
                entry.inputs = [str(i) for i in entry.inputs]
            if entry.input is not None:
                entry.input = str(entry.input)
            if entry.select is not None:
                entry.select = str(entry.select)
            module.glue_logic.append(entry)

        # Parse interconnects
        for ic in data.get('interconnects', []):
            module.interconnects.append(self._build_interconnect(ic))

        # Parse address_decode
        if 'address_decode' in data:
            module.address_decode = self._build_address_decode(data['address_decode'])

        # Parse firmware
        if 'firmware' in data:
            module.firmware = self._build_firmware(data['firmware'])

        # Parse build_info
        if 'build_info' in data:
            module.build_info = self._build_build_info(data['build_info'])

        # Parse srams — store raw values (may contain $PARAM references)
        for s in data.get('srams', []):
            unit = s.get('addr_unit')
            if unit is not None and unit not in SRAM_ADDR_UNITS:
                raise ValueError(
                    f"module '{module.name}': srams entry '{s.get('name', '')}': addr_unit "
                    f"{unit!r} is not one of {', '.join(SRAM_ADDR_UNITS)}")
            entry = SramEntry(
                name=s.get('name', ''),
                addr_width=s.get('addr_width', 14),
                data_width=s.get('data_width', 32),
                desc=s.get('desc', ''),
                addr_unit=unit,
            )
            try:
                entry.unit
            except ValueError as e:
                raise ValueError(f"module '{module.name}': {e}") from None
            module.srams.append(entry)

        # Parse the per-module constraints: block (pre-verified-IP timing
        # intent the constraints backend aggregates to the top SDC/XDC).
        if 'constraints' in data and isinstance(data['constraints'], dict):
            module.constraints = self._build_constraints(data['constraints'])

        self._built_modules[name] = module
        return module

    def _build_constraints(self, data: Dict[str, Any]) -> ConstraintSet:
        """Build a ConstraintSet from a module's ``constraints:`` YAML dict.

        Every sub-entry may carry an optional ``scope`` (both | asic/sdc |
        fpga/xdc); the per-entry scope is stashed on a private ``_scope``
        attribute consumed by SoCConstraintsBackend.  Lists default empty so a
        partial constraints: block is inert.
        """
        cs = ConstraintSet(scope=str(data.get('scope', 'both')))

        def _scope(entry: Dict[str, Any], default: str):
            return str(entry.get('scope', default))

        default_scope = cs.scope

        for c in data.get('clocks', []):
            obj = Clock(
                name=c.get('name', ''),
                source=c.get('source', ''),
                desc=c.get('desc', ''),
                freq_hz=c.get('freq_hz'),
                period_ns=c.get('period_ns'),
                port=c.get('port'),
            )
            obj._scope = _scope(c, default_scope)
            cs.clocks.append(obj)

        for g in data.get('generated_clocks', []):
            obj = GeneratedClock(
                name=g.get('name', ''),
                source=g.get('source', ''),
                divide_by=g.get('divide_by'),
                multiply_by=g.get('multiply_by'),
                source_pin=g.get('source_pin'),
                target_pin=g.get('target_pin'),
                desc=g.get('desc', ''),
            )
            obj._scope = _scope(g, default_scope)
            cs.generated_clocks.append(obj)

        for d in data.get('io_delays', []):
            ports = d.get('ports', d.get('port'))
            if isinstance(ports, str):
                ports = [ports]
            obj = IoDelay(
                kind=d.get('kind', 'input'),
                clock=d.get('clock', ''),
                ports=[str(p) for p in (ports or [])],
                min=d.get('min'),
                max=d.get('max'),
                desc=d.get('desc', ''),
            )
            obj._scope = _scope(d, default_scope)
            cs.io_delays.append(obj)

        for f in data.get('false_paths', []):
            obj = FalsePath(
                from_=f.get('from'),
                to=f.get('to'),
                through=f.get('through'),
                hold=bool(f.get('hold', False)),
                desc=f.get('desc', ''),
            )
            obj._scope = _scope(f, default_scope)
            cs.false_paths.append(obj)

        for m in data.get('multicycle_paths', []):
            obj = MulticyclePath(
                cycles=int(m.get('cycles', 2)),
                setup=bool(m.get('setup', False)),
                hold=bool(m.get('hold', False)),
                end=bool(m.get('end', False)),
                from_=m.get('from'),
                to=m.get('to'),
                through=m.get('through'),
                desc=m.get('desc', ''),
            )
            obj._scope = _scope(m, default_scope)
            cs.multicycle_paths.append(obj)

        for cg in data.get('clock_groups', []):
            groups = cg.get('groups', [])
            # Each group is a list of clock names; accept a bare string too.
            norm = []
            for grp in groups:
                if isinstance(grp, str):
                    norm.append([grp])
                else:
                    norm.append([str(x) for x in grp])
            obj = ClockGroup(
                name=cg.get('name', ''),
                groups=norm,
                desc=cg.get('desc', ''),
            )
            obj._scope = _scope(cg, default_scope)
            cs.clock_groups.append(obj)

        return cs

    def _build_interface(self, data: Dict[str, Any]) -> Interface:
        """Build an Interface from a YAML dict."""
        return Interface(
            name=data.get('name', ''),
            type=data.get('type', 'wire'),
            direction=data.get('direction', 'in'),
            params=data.get('params', {}),
            desc=data.get('desc', ''),
        )

    def _build_instance(self, data: Dict[str, Any]) -> Instance:
        """Build an Instance from a YAML dict."""
        is_rtl = 'rtl_module' in data
        module_name = data.get('rtl_module', data.get('module', ''))

        inst = Instance(
            instance_name=data.get('instance_name', ''),
            module_name=module_name,
            is_rtl_module=is_rtl,
            addressable=data.get('addressable', False),
            condition=data.get('condition'),
            params=data.get('params', {}),
        )

        # Parse connections
        for conn in data.get('connections', []):
            inst.connections.append(Connection(
                port=str(conn.get('port', '')),
                conn=str(conn.get('conn', '')),
                desc=conn.get('desc', ''),
            ))

        # Parse inline interfaces (for rtl_module)
        for iface in data.get('interfaces', []):
            inst.inline_interfaces.append(self._build_interface(iface))

        return inst

    def _build_interconnect(self, data: Dict[str, Any]) -> Interconnect:
        """Build an Interconnect from a YAML dict."""
        ic = Interconnect(
            name=data.get('name', ''),
            gen=data.get('gen', True),
            type=data.get('type', 'ahb_lite'),
            desc=data.get('desc', ''),
            params=data.get('params', {}),
            perf_probes=data.get('perf_probes'),
            arbitration_scheme=data.get('arbitration_scheme'),
            # As written: an ``impl:`` that is present but empty or null is
            # refused by the validator / AHB backend, never read as the default.
            impl=data['impl'] if 'impl' in data else 'busmatrix',
        )
        # Keys this method does not read are ignored. Record them (a plain
        # attribute, not a dataclass field, so no model dump, report or
        # schema doc changes) for backends.cmsdk_mux.check_interconnect_keys:
        # a misspelt `impl` key must not silently build a bus matrix.
        ic._unknown_yaml_keys = {k: v for k, v in data.items()
                                 if k not in INTERCONNECT_YAML_KEYS}

        # Parse connections (clock, reset, scan, remap, etc.)
        for conn in data.get('connections', []):
            ic.connections.append(Connection(
                port=str(conn.get('port', '')),
                conn=str(conn.get('conn', '')),
                desc=conn.get('desc', ''),
            ))

        for t in data.get('targets', []):
            target = InterconnectTarget(
                name=t.get('name', '') if isinstance(t, dict) else str(t),
                instance=t.get('instance') if isinstance(t, dict) else None,
                base=t.get('base', 0) if isinstance(t, dict) else 0,
                size=t.get('size', 0) if isinstance(t, dict) else 0,
                phys_size=t.get('phys_size') if isinstance(t, dict) else None,
                sw_access=t.get('sw_access', 'rw') if isinstance(t, dict) else 'rw',
                region_type=t.get('region_type') if isinstance(t, dict) else None,
                role=t.get('role') if isinstance(t, dict) else None,
                subordinate_bus=t.get('subordinate_bus', False) if isinstance(t, dict) else False,
                protocol=t.get('protocol', 'ahb').lower() if isinstance(t, dict) else 'ahb',
                apb_config=t.get('apb_config') if isinstance(t, dict) else None,
                passthrough=t.get('passthrough') if isinstance(t, dict) else None,
                probe=bool(t.get('probe', False)) if isinstance(t, dict) else False,
                register_map=t.get('register_map') if isinstance(t, dict) else None,
                desc=t.get('desc', '') if isinstance(t, dict) else '',
            )
            if isinstance(t, dict):
                _record_unknown(target, t, INTERCONNECT_TARGET_YAML_KEYS)
            # Resolve a target's register_map exactly as an AddressDecodeSlot's
            # is resolved, so a peripheral reached directly as an interconnect
            # target (e.g. the rendered DMA-250 at dmac_ctrl) is not invisible
            # to the register model.
            if target.register_map:
                target.resolved_register_map = self._build_register_map(
                    target.register_map
                )
            ic.targets.append(target)

        for init in data.get('initiators', []):
            targets_list = init.get('targets', [])
            init_targets = []
            for tgt in targets_list:
                if isinstance(tgt, dict):
                    init_targets.append(InterconnectInitiatorTarget(
                        name=tgt.get('name', ''),
                        visibility=tgt.get('visibility'),
                    ))
                    _record_unknown(init_targets[-1], tgt, INITIATOR_TARGET_YAML_KEYS)
                else:
                    init_targets.append(InterconnectInitiatorTarget(name=str(tgt)))

            ic.initiators.append(InterconnectInitiator(
                name=init.get('name', ''),
                instance=init.get('instance'),
                passthrough=init.get('passthrough'),
                probe=bool(init.get('probe', False)),
                targets=init_targets,
            ))
            _record_unknown(ic.initiators[-1], init, INTERCONNECT_INITIATOR_YAML_KEYS)

        return ic

    def _build_address_decode(self, data: Dict[str, Any]) -> AddressDecode:
        """Build an AddressDecode from a YAML dict."""
        ad = AddressDecode(
            type=data.get('type', ''),
            module=data.get('module', ''),
            bridge=data.get('bridge'),
        )

        for s in data.get('slots', []):
            slot = AddressDecodeSlot(
                slot=s.get('slot', 0),
                name=s.get('name', ''),
                module=s.get('module', ''),
                offset=s.get('offset', 0),
                size=s.get('size', 0),
                register_map=s.get('register_map'),
                desc=s.get('desc', ''),
            )
            # Load and attach register map if referenced
            if slot.register_map:
                slot.resolved_register_map = self._build_register_map(
                    slot.register_map
                )
            if 'address_decode' in s:
                slot.address_decode = self._build_address_decode(s['address_decode'])
            ad.slots.append(slot)

        return ad

    def _build_register_map(self, filename: str) -> Optional[RegisterMap]:
        """Load and build a RegisterMap from a YAML file path."""
        data = self.parser.parse_register_map(filename)
        if not data or 'register_map' not in data:
            return None

        rm_data = data['register_map']
        rm = RegisterMap(
            name=rm_data.get('name', ''),
            module=rm_data.get('module', ''),
            gen=rm_data.get('gen', False),
            desc=rm_data.get('description', ''),
            address_width=rm_data.get('address_width', 12),
            data_width=rm_data.get('data_width', 32),
            source_file=filename,
        )

        for reg in rm_data.get('registers', []):
            raw_offset = reg.get('offset', 0)
            if isinstance(raw_offset, str):
                raw_offset = int(raw_offset, 0)
            r = Register(
                name=reg.get('name', ''),
                offset=raw_offset,
                width=reg.get('width', 32),
                access=reg.get('access', 'RW'),
                reset_value=reg.get('reset_value'),
                desc=reg.get('description', ''),
            )
            for fld in reg.get('fields', []):
                r.fields.append(RegisterField(
                    name=fld.get('name', ''),
                    bits=str(fld.get('bits', '')),
                    access=fld.get('access', 'RO'),
                    reset_value=fld.get('reset_value'),
                    desc=fld.get('description', ''),
                ))
            rm.registers.append(r)

        return rm

    def _build_firmware(self, data: Dict[str, Any]) -> Firmware:
        """Build a Firmware from a YAML dict."""
        fw = Firmware(
            cpu_initiator=data.get('cpu_initiator', ''),
            cpu=data.get('cpu', ''),
            adp=data.get('adp'),
            hex_adjust=data.get('hex_adjust'),
        )

        for lp in data.get('linker_profiles', []):
            profile = LinkerProfile(name=lp.get('name', ''))
            for i, r in enumerate(lp.get('regions', [])):
                where = f"firmware: linker profile '{profile.name}' region {i}"
                if not isinstance(r, dict):
                    raise ValueError(f"{where}: expected a mapping of keys, got {r!r}")
                where += f" (target '{r.get('target', '')}')"
                unknown = sorted(set(r) - LINKER_REGION_YAML_KEYS, key=str)
                if unknown:
                    hints = []
                    for k in unknown:
                        guess = difflib.get_close_matches(str(k), LINKER_REGION_YAML_KEYS, n=1)
                        hints.append(f"'{k}'" + (f" (did you mean '{guess[0]}'?)" if guess else ""))
                    raise ValueError(
                        f"{where}: unknown key {', '.join(hints)}; a linker region takes "
                        f"only {', '.join(sorted(LINKER_REGION_YAML_KEYS))}")
                origin = r.get('origin')
                profile.regions.append(LinkerRegion(
                    target=r.get('target', ''),
                    address_select=r.get('address_select', 'default'),
                    linker_name=r.get('linker_name'),
                    software_access=r.get('software_access'),
                    size_adjust=r.get('size_adjust'),
                    phys_size=r.get('phys_size'),
                    origin=None if origin is None else linker_origin(origin, where),
                ))
            fw.linker_profiles.append(profile)

        return fw

    def _build_build_info(self, data: Dict[str, Any]) -> BuildInfo:
        """Build a BuildInfo from a YAML dict."""
        bi = BuildInfo(
            apb_slot=data.get('apb_slot', 3),
            gen=data.get('gen', True),
        )
        for f in data.get('fields', []):
            bi.fields.append(BuildInfoField(
                name=f.get('name', ''),
                type=f.get('type', 'int32'),
                value=f.get('value'),
                source=f.get('source'),
                max_length=f.get('max_length', 4),
                desc=f.get('desc', ''),
            ))
        return bi

    def _build_pad_tech(self, data: Dict[str, Any]) -> PadTech:
        """Build a PadTech from a pad-tech YAML dict.

        Accepts either the unwrapped descriptor or the wrapped
        ``{'pad_tech': {...}}`` form (mirroring how parse_pad_tech returns
        the raw dict with its top-level key intact).
        """
        if 'pad_tech' in data and isinstance(data['pad_tech'], dict):
            data = data['pad_tech']

        def _cell(key: str) -> Optional[PadCell]:
            cd = data.get(key)
            if not isinstance(cd, dict):
                return None
            return PadCell(
                module=cd.get('module', ''),
                port_map=dict(cd.get('port_map', {})),
            )

        pt = PadTech(
            name=data.get('name', ''),
            flavour=data.get('flavour', 'asic'),
            gpio_tio=data.get('gpio_tio', 4),
            bidir_cell=_cell('bidir_cell'),
            input_cell=_cell('input_cell'),
            bidir_io_cell=_cell('bidir_io_cell'),
            output_cell=_cell('output_cell'),
            power_cells=dict(data.get('power_cells', {})),
            supply_connect=data.get('supply_connect', True),
            align_ports=data.get('align_ports', False),
            upf=data.get('upf'),
        )
        if 'constants' in data and isinstance(data['constants'], dict):
            pt.constants = dict(data['constants'])
        return pt

    def _build_pin_map(self, data: Dict[str, Any]) -> PinMap:
        """Build a PinMap from a pin-map YAML dict.

        Accepts either the unwrapped descriptor or the wrapped
        ``{'pin_map': {...}}`` form.  power_pads entries are constructed as
        PowerPad(**entry); the cell's supply_port is resolved later by the
        backend from PadTech.power_cells (left None here).
        """
        if 'pin_map' in data and isinstance(data['pin_map'], dict):
            data = data['pin_map']

        power_pads = []
        for entry in data.get('power_pads', []):
            if isinstance(entry, dict):
                power_pads.append(PowerPad(
                    cell=entry.get('cell', ''),
                    prefix=entry.get('prefix', ''),
                    count=entry.get('count', 1),
                    index_base=entry.get('index_base', 0),
                    net=entry.get('net'),
                    supply_port=entry.get('supply_port'),
                    commented=entry.get('commented', False),
                    supply_connect=entry.get('supply_connect'),
                    pin_side=entry.get('pin_side', entry.get('side')),
                ))

        # MIXED-I/O mode: explicit per-signal pad list (additive; default empty).
        # `pads:` may be a flat list OR a group-name -> list mapping (grouped for
        # readability — like the chip-boundary spec); both flatten to PadSignal.
        def _padsig(entry: Dict[str, Any]) -> PadSignal:
            return PadSignal(
                name=entry.get('name', entry.get('chip_port', '')),
                kind=entry.get('kind', entry.get('pad_kind', 'input')),
                width=int(entry.get('width', 1)),
                pad=entry.get('pad'),
                in_net=entry.get('in'),
                out_net=entry.get('out'),
                oe_net=entry.get('oe'),
                oe_polarity=entry.get('oe_polarity', 'active_high'),
                pin_side=entry.get('pin_side', entry.get('side')),
            )

        pads: List[PadSignal] = []
        raw_pads = data.get('pads', [])
        if isinstance(raw_pads, dict):
            for _group, items in raw_pads.items():
                for entry in (items or []):
                    if isinstance(entry, dict):
                        pads.append(_padsig(entry))
        else:
            for entry in raw_pads:
                if isinstance(entry, dict):
                    pads.append(_padsig(entry))

        mode = data.get('mode', 'mixed' if pads else 'gpio')

        return PinMap(
            tech=data.get('tech', ''),
            pin_count=data.get('pin_count', 0),
            variant=data.get('variant'),
            body_flavour=data.get('body_flavour', 'cfg'),
            gpio_net_template=data.get('gpio_net_template', 'pad_gpio_port{idx}'),
            gpio_port_width=data.get('gpio_port_width', 8),
            has_se=data.get('has_se', True),
            pre_module=data.get('pre_module', ''),
            boundary_supplies=data.get('boundary_supplies', 'none'),
            supply_ports=list(data.get('supply_ports', [])),
            supply_connect=data.get('supply_connect'),
            gpio_tio=data.get('gpio_tio', 4),
            trailing_comma_bug=data.get('trailing_comma_bug', False),
            internal_vssio_wire=data.get('internal_vssio_wire', False),
            extra_wires=list(data.get('extra_wires', [])),
            bonded_gpio={k: list(v) for k, v in data.get('bonded_gpio', {}).items()},
            tie_off={k: list(v) for k, v in data.get('tie_off', {}).items()},
            power_pads=power_pads,
            ring=data.get('ring'),
            uart_txd_tie=data.get('uart_txd_tie', True),
            chip_power_pins=data.get('chip_power_pins', False),
            power_pins_guard=data.get('power_pins_guard', False),
            ctrl_ren=dict(data.get('ctrl_ren', {})),
            ctrl_inout=data.get('ctrl_inout', False),
            test_inst_name=data.get('test_inst_name', 'uPAD_TEST_I'),
            mode=mode,
            chip_module=data.get('chip_module'),
            pads=pads,
        )

    def _build_chip_boundary(self, data: Dict[str, Any]) -> ChipBoundary:
        """Build a ChipBoundary from a chip-boundary YAML dict.

        Accepts either the unwrapped descriptor or the wrapped
        ``{'chip_boundary': {...}}`` form (mirroring parse_chip_boundary).

        ``pads:`` may be a flat list of pad dicts OR a mapping of
        group-name -> list-of-pad-dicts (the bundled multicore spec groups
        pads as clk/swd/qspi/...); both forms flatten to ChipPad rows, with
        the group label preserved for emission comments.
        """
        if 'chip_boundary' in data and isinstance(data['chip_boundary'], dict):
            data = data['chip_boundary']

        def _pad(entry: Dict[str, Any], group: str = '') -> ChipPad:
            return ChipPad(
                chip_port=entry.get('chip_port', ''),
                kind=entry.get('pad_kind', entry.get('kind', 'input')),
                width=int(entry.get('width', 1)),
                in_net=entry.get('in'),
                out_net=entry.get('out'),
                oe_net=entry.get('oe'),
                oe_polarity=entry.get('oe_polarity', 'active_high'),
                group=group,
            )

        pads: List[ChipPad] = []
        raw_pads = data.get('pads', [])
        if isinstance(raw_pads, dict):
            for group, items in raw_pads.items():
                for entry in (items or []):
                    if isinstance(entry, dict):
                        pads.append(_pad(entry, group))
        else:
            for entry in raw_pads:
                if isinstance(entry, dict):
                    pads.append(_pad(entry))

        ties = []
        for entry in data.get('ties', []):
            if isinstance(entry, dict):
                ties.append({
                    'soc_port': entry.get('soc_port', entry.get('port', '')),
                    'const': entry.get('const', entry.get('value', "1'b0")),
                })

        opens = [str(o) for o in data.get('opens', data.get('open', []))]

        terminators = []
        for entry in data.get('terminators', []):
            if not isinstance(entry, dict):
                continue
            module = entry.get('module', '')
            instance = entry.get('instance', '')
            if not module or not instance:
                raise ValueError(
                    "chip_boundary terminator needs both 'module' and "
                    f"'instance' (got module={module!r}, instance={instance!r})")
            terminators.append(ChipTerminator(
                module=module,
                instance=instance,
                params={str(k): v for k, v in (entry.get('params') or {}).items()},
                soc_ports={str(k): str(v)
                           for k, v in (entry.get('soc_ports') or {}).items()},
                widths={str(k): int(v)
                        for k, v in (entry.get('widths') or {}).items()},
                bonded_refs={str(k): str(v)
                             for k, v in (entry.get('bonded_refs') or {}).items()},
                desc=entry.get('desc', ''),
            ))

        return ChipBoundary(
            chip_module=data.get('chip_module', data.get('name', '')),
            inner_module=data.get('inner_module', data.get('soc_module', '')),
            pads=pads,
            ties=ties,
            opens=opens,
            terminators=terminators,
        )

    def _build_fpga_profile(self, data: Dict[str, Any],
                            name: str = '') -> FpgaProfile:
        """Build an FpgaProfile from an fpga-profile YAML dict.

        Accepts either the unwrapped descriptor or the wrapped
        ``{'fpga_profile': {...}}`` form (mirroring parse_fpga_profile).
        Every field defaults empty, so a partial/absent profile is inert.
        """
        if 'fpga_profile' in data and isinstance(data['fpga_profile'], dict):
            data = data['fpga_profile']

        def _norm_param(entry: Dict[str, Any]) -> Dict[str, str]:
            return {
                'name': entry.get('name', ''),
                'default': str(entry.get('default', '')),
                'comment': entry.get('comment', ''),
            }

        def _norm_tap(entry: Dict[str, Any]) -> Dict[str, str]:
            return {
                'name': entry.get('name', ''),
                'from': entry.get('from', ''),
                'kind': entry.get('kind', 'soc_out'),   # soc_out | wire
                'comment': entry.get('comment', ''),
            }

        return FpgaProfile(
            name=name or data.get('name', ''),
            module_name=data.get('module_name', ''),
            tristate_convention=data.get('tristate_convention', 'vivado'),
            param_overrides=[_norm_param(e) for e in data.get('param_overrides', [])
                             if isinstance(e, dict)],
            port_renames=dict(data.get('port_renames', {}) or {}),
            exclude_pads=[str(p) for p in data.get('exclude_pads', [])],
            exclude_ties={str(k): str(v)
                          for k, v in (data.get('exclude_ties', {}) or {}).items()},
            debug_taps=[_norm_tap(e) for e in data.get('debug_taps', [])
                        if isinstance(e, dict)],
            features=dict(data.get('features', {}) or {}),
            ip_xact=dict(data.get('ip_xact', {}) or {}),
        )

    def _resolve_instances(self, module: Module):
        """Resolve module references for all instances."""
        for inst in module.instances:
            if inst.is_rtl_module:
                # RTL modules have inline interfaces — create a stub Module
                stub = Module(
                    name=inst.module_name,
                    gen=False,
                    desc=f'Standalone RTL module: {inst.module_name}',
                )
                for iface in inst.inline_interfaces:
                    stub.interfaces.append(iface)
                inst.resolved_module = stub
            else:
                # Look up YAML module file
                mod_data = self.parser.parse_module(inst.module_name)
                if mod_data and 'module' in mod_data:
                    child = self._build_module(
                        mod_data['module'],
                        source_file=f'modules/{inst.module_name}.yaml',
                    )
                    inst.resolved_module = child
                    # Recurse into child instances
                    self._resolve_instances(child)
