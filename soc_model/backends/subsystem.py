"""Subsystem SV module backend — generates structural SV for subsystem modules.

Extends SoCTopLevelBackend to handle subsystem-specific patterns:
  - Internal bus wires use 'i_' prefix to avoid collision with module ports
  - Passthrough targets/initiators connect directly to module-level ports
  - Unconnected interconnect ports (e.g. system_hsel) are handled correctly

Generated files:
  - <module_name>.sv — structural subsystem module
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..model import Instance, Interconnect, Interface, Module
from ..utils import parse_conn_ref
from .protocol_utils import (
    AHB_INITIATOR_SIGNALS, AHB_TARGET_SIGNALS,
    passthrough_initiator_uses_initiator_boundary,
    param_width_str as _param_width_str,
    width_str as _width_str,
)
from .toplevel import SoCTopLevelBackend


class SoCSubsystemBackend(SoCTopLevelBackend):
    """Generates structural SV for a subsystem module.

    Inherits most logic from SoCTopLevelBackend. Overrides bus wire generation
    and interconnect rendering to handle the 'i_' prefix convention and
    passthrough connections.
    """

    def __init__(self, subsystem_module: Module):
        super().__init__(subsystem_module)
        # Pre-compute passthrough mappings from interconnect metadata
        self._init_passthrough_maps()

    def _init_passthrough_maps(self):
        """Build lookup dicts for passthrough targets and initiators."""
        self._passthrough_targets = {}   # ic_target_name -> module_iface_name
        self._passthrough_initiators = {}  # ic_init_name -> module_iface_name

        for ic in self.top.interconnects:
            for tgt in ic.targets:
                if tgt.passthrough:
                    self._passthrough_targets[tgt.name] = tgt.passthrough
            for init in ic.initiators:
                if init.passthrough:
                    self._passthrough_initiators[init.name] = init.passthrough

    def _expand_ahb_port(self, iface: Interface, ports: list):
        """Expand an AHB interface into individual port signals.

        Three cases:

        1. Passthrough-initiator endpoint — an iface declared
           `direction: target` that is ALSO the `passthrough` of an
           interconnect *initiator* (an external AHB master that drives
           transactions INTO the subsystem, e.g. eth_ss_0/eth_ss_1).
           The boundary form depends on whether the iface excludes the
           bare `hready` (see
           passthrough_initiator_uses_initiator_boundary):

             - no `hready` in EXCLUDE: the generated matrix wrapper
               exposes the cmsdk *initiator* boundary (no hsel / no
               hreadyout; hrdata/hready/hresp are subsystem outputs).
               The subsystem module boundary MUST match that exactly —
               otherwise the top-level instantiation leaves hsel
               dangling, hreadyout undriven, and hready
               direction-conflicts. This is the eth_ss_0/eth_ss_1 case
               (the ASIC golden gate netlist confirms single-slave form).
               Emit AHB_INITIATOR_SIGNALS with directions inverted to
               the receiving (slave) end.

             - `hready` in EXCLUDE: the designer explicitly wants the
               AHB-Lite *slave/target* boundary minus the bare `hready`
               (hsel + addr-phase in; hrdata/hresp/hreadyout out). This
               is the cpu_ss case (hand-written nanosoc_ss_cpu.v
               reference + the multicore parent instantiation both use
               this mux form). Fall through to case 2 — the target
               path already drops hready via `exclude`.

        2. True target (subsystem IS the addressable slave) — use the
           raw AHB_TARGET_SIGNALS directions (hsel/hready in,
           hresp/hreadyout out).

        3. Initiator (subsystem drives the master) — use the raw
           AHB_INITIATOR_SIGNALS directions.
        """
        is_target = iface.direction in ('target', 'in')
        # Values of _passthrough_initiators are module-iface names that
        # an interconnect initiator passes straight through to. Only the
        # no-hready-exclude variant takes the cmsdk *initiator* boundary;
        # the hready-excluded variant (cpu_ss) takes the slave/target
        # boundary minus hready (matching nanosoc_ss_cpu.v + the parent).
        is_passthrough_initiator = (
            iface.name in self._passthrough_initiators.values()
            and passthrough_initiator_uses_initiator_boundary(iface.params)
        )
        addr_w = iface.params.get('ADDR_WIDTH', iface.params.get('ADDR_W', 'SYS_ADDR_W'))
        data_w = iface.params.get('DATA_WIDTH', iface.params.get('DATA_W', 'SYS_DATA_W'))
        exclude = set(iface.params.get('EXCLUDE', []))

        if is_passthrough_initiator:
            # Subsystem is the receiving (slave) end of an external
            # initiator. Invert the initiator-perspective directions:
            #   initiator drives  ('out') -> subsystem input
            #   initiator receives ('in') -> subsystem output
            # AHB_INITIATOR_SIGNALS has no hsel/hreadyout, so those
            # spurious slave-boundary signals never get emitted.
            signals = AHB_INITIATOR_SIGNALS
            for sig_name, sig_dir, sig_width in signals:
                if sig_name in exclude:
                    continue
                w = sig_width
                if w == 'SYS_ADDR_W':
                    w = addr_w
                elif w == 'SYS_DATA_W':
                    w = data_w
                direction = 'input' if sig_dir == 'out' else 'output'
                ports.append({
                    'name': f'{iface.name}_{sig_name}',
                    'direction': direction,
                    'width': w,
                    'width_str': _param_width_str(w),
                    'desc': f'{iface.desc} — {sig_name}',
                })
        elif is_target:
            # Subsystem IS the target — use target signal directions as-is
            signals = AHB_TARGET_SIGNALS
            for sig_name, sig_dir, sig_width in signals:
                if sig_name in exclude:
                    continue
                w = sig_width
                if w == 'SYS_ADDR_W':
                    w = addr_w
                elif w == 'SYS_DATA_W':
                    w = data_w
                # Target receives address-phase (in), drives response (out)
                direction = 'input' if sig_dir == 'in' else 'output'
                ports.append({
                    'name': f'{iface.name}_{sig_name}',
                    'direction': direction,
                    'width': w,
                    'width_str': _param_width_str(w),
                    'desc': f'{iface.desc} — {sig_name}',
                })
        else:
            # Subsystem drives an initiator port — use initiator signal directions as-is
            signals = AHB_INITIATOR_SIGNALS
            for sig_name, sig_dir, sig_width in signals:
                w = sig_width
                if w == 'SYS_ADDR_W':
                    w = addr_w
                elif w == 'SYS_DATA_W':
                    w = data_w
                direction = 'output' if sig_dir == 'out' else 'input'
                ports.append({
                    'name': f'{iface.name}_{sig_name}',
                    'direction': direction,
                    'width': w,
                    'width_str': _param_width_str(w),
                    'desc': f'{iface.desc} — {sig_name}',
                })

    def _is_passthrough_target(self, name: str) -> bool:
        return name in self._passthrough_targets

    def _ic_target_wire(self, ic_port: str, sig: str) -> str:
        # Internal target nets carry the 'i_' prefix here (see
        # _expand_bus_connection), so a nested child subsystem's link gate
        # (SoCTopLevelBackend._child_link_htrans_gates) reads i_<tgt>_hsel.
        return f'i_{ic_port}_{sig}'

    def _is_passthrough_initiator(self, name: str) -> bool:
        return name in self._passthrough_initiators

    def _build_bus_wires(self) -> List[Dict[str, Any]]:
        """Build internal bus wire bundles with 'i_' prefix.

        Passthrough targets/initiators do NOT get internal wires — they connect
        directly to module-level ports.
        """
        bus_wires = []
        seen = set()

        for inst in self.top.instances:
            if inst.resolved_module is None:
                continue
            for conn in inst.connections:
                if conn.is_unconnected:
                    continue
                inst_ref, port_name, high, low = parse_conn_ref(conn.conn)
                if inst_ref and any(ic.name == inst_ref for ic in self.top.interconnects):
                    ic = next(ic for ic in self.top.interconnects if ic.name == inst_ref)
                    wire_prefix = port_name

                    if wire_prefix in seen:
                        continue
                    seen.add(wire_prefix)

                    # Check if this is an initiator port
                    is_init = any(init.name == port_name for init in ic.initiators)

                    if is_init:
                        # Initiator ports that are passthroughs don't need internal wires
                        if self._is_passthrough_initiator(port_name):
                            continue
                        for sig, sig_dir, sig_w in AHB_INITIATOR_SIGNALS:
                            bus_wires.append({
                                'name': f'i_{wire_prefix}_{sig}',
                                'width_str': _param_width_str(sig_w),
                                'desc': f'{wire_prefix} AHB initiator — {sig}',
                            })
                    else:
                        # Passthrough targets get their system_* intermediate wires
                        # emitted at the END of this method (the passthrough boundary
                        # is the subsystem port, not reached by this instance scan).
                        if self._is_passthrough_target(port_name):
                            continue
                        for sig, sig_dir, sig_w in AHB_TARGET_SIGNALS:
                            # Map 'hready' to 'hreadymux' to match interconnect port naming
                            wire_sig = 'hreadymux' if sig == 'hready' else sig
                            bus_wires.append({
                                'name': f'i_{wire_prefix}_{wire_sig}',
                                'width_str': _param_width_str(sig_w),
                                'desc': f'{wire_prefix} AHB target — {wire_sig}',
                            })

        # Passthrough-target intermediate wires (system_hsel/haddr/htrans/...).
        # Emitted directly from each interconnect's passthrough targets — the
        # passthrough boundary is the subsystem's own port (not an instance), so
        # it is NOT reached by the instance-connection scan above. The
        # interconnect drives these wires; _build_passthrough_tieoffs forwards
        # them to the cpu_<n>_* ports, gating HTRANS by system_hsel. hready is the
        # interconnect's hreadymux output and is not threaded through a wire here.
        for ic in self.top.interconnects:
            for tgt in ic.targets:
                if not tgt.passthrough or tgt.name in seen:
                    continue
                seen.add(tgt.name)
                for sig, sig_dir, sig_w in AHB_TARGET_SIGNALS:
                    if sig == 'hready':
                        continue
                    bus_wires.append({
                        'name': f'{tgt.name}_{sig}',
                        'width_str': _param_width_str(sig_w),
                        'desc': f'{tgt.name} passthrough — {sig}',
                    })

        # HSEL-qualified HTRANS for target-boundary passthrough initiators
        # (cpu_ss): driven in _build_passthrough_tieoffs, read by the
        # interconnect's initiator htrans port in _build_interconnect.
        for init_name in self._hsel_gated_passthrough_initiators():
            bus_wires.append({
                'name': f'{init_name}_htrans_gated',
                'width_str': _param_width_str(2),
                'desc': f'{init_name} passthrough initiator — htrans qualified by hsel',
            })

        # HSEL-qualified HTRANS for links from this module's matrix into a
        # nested child subsystem's passthrough initiator (toplevel.py
        # _child_link_htrans_gates).
        bus_wires.extend(self._child_link_htrans_gate_wires())
        return bus_wires

    def _expand_bus_connection(self, inst: Instance, inst_data: Dict, port: str,
                               ic_port: str, ic_name: str):
        """Expand a bus interface connection into individual signal connections.

        Uses 'i_' prefix for internal wires. Passthrough ports are not expected
        to be reached here (they connect at the interconnect level, not through
        instance connections).
        """
        ic = next(ic for ic in self.top.interconnects if ic.name == ic_name)

        is_init = any(init.name == ic_port for init in ic.initiators)

        if is_init:
            signals = AHB_INITIATOR_SIGNALS
            # An `ahb_master` connection expands to BARE uppercase ports
            # (HADDR, HTRANS, ...) by default — the cortex-m0 (slcorem0p) master
            # convention. A module whose master ports are PREFIXED (e.g.
            # ethmac_subsystem_ahb's ahb_master_haddr) sets
            # INST_PORT_PREFIXED:True on its ahb_master interface so the bundle
            # expands to ahb_master_<sig> instead. Backward-compatible: absent
            # the flag, behaviour is unchanged (bare).
            init_iface = self._find_instance_interface(inst, port)
            master_prefixed = bool(
                init_iface and init_iface.params.get('INST_PORT_PREFIXED'))
            for sig, sig_dir, _ in signals:
                if port == 'ahb_master':
                    inst_port = f'{port}_{sig}' if master_prefixed else sig.upper()
                elif port.isupper():
                    inst_port = f'{port}_{sig.upper()}'
                else:
                    inst_port = f'{port}_{sig}'
                wire_name = f'i_{ic_port}_{sig}'
                inst_data['connections'].append({
                    'port': inst_port,
                    'signal': wire_name,
                })
        else:
            signals = AHB_TARGET_SIGNALS
            inst_iface = self._find_instance_interface(inst, port)
            exclude = set(inst_iface.params.get('EXCLUDE', [])) if inst_iface else set()
            # Same knob the initiator branch honours for ahb_master bundles:
            # a module whose RTL keeps PREFIXED slave ports sets
            # INST_PORT_PREFIXED:True on the iface to opt out of the bare
            # `ahb_slave` convention. Absent the flag, behaviour is unchanged.
            slave_prefixed = bool(
                inst_iface and inst_iface.params.get('INST_PORT_PREFIXED'))
            # A nested child subsystem's passthrough initiator takes the
            # hsel-qualified HTRANS (toplevel.py _child_link_htrans_gates).
            gated_htrans = self._child_link_htrans_gate_for(inst, port)
            for sig, sig_dir, _ in signals:
                if sig in exclude:
                    continue
                if port == 'ahb_slave' and not slave_prefixed:
                    # Bare uppercase port names (e.g. HSEL, HADDR)
                    inst_port = sig.upper()
                elif port.isupper():
                    inst_port = f'{port}_{sig.upper()}'
                else:
                    inst_port = f'{port}_{sig}'
                # Map 'hready' to 'hreadymux' to match interconnect port naming
                wire_sig = 'hreadymux' if sig == 'hready' else sig
                wire_name = f'i_{ic_port}_{wire_sig}'
                if sig == 'htrans' and gated_htrans:
                    wire_name = gated_htrans
                inst_data['connections'].append({
                    'port': inst_port,
                    'signal': wire_name,
                })

    def _build_interconnect(self) -> Optional[Dict[str, Any]]:
        """Build interconnect instance data with passthrough and 'i_' prefix handling.

        For the CPU subsystem interconnect:
          - Internal initiator ports (cpu_0) use i_-prefixed wires
          - Passthrough initiator ports (cpu_ss) use module port names directly,
            except HTRANS of a target-boundary one, which takes the
            hsel-qualified {init}_htrans_gated wire
            (_hsel_gated_passthrough_initiators)
          - Internal target ports (bootrom_0, imem_0, dmem_0) use i_-prefixed wires
          - Passthrough target ports (system) use module port names directly
          - Passthrough target's hsel and hreadymux are left unconnected
        """
        if not self.top.interconnects:
            return None

        ic = self.top.interconnects[0]
        # Module name follows AHB backend convention: strip '_ahb_interconnect' and append '_interconnect'
        module_prefix = ic.name.replace('_ahb_interconnect', '').replace('_interconnect', '')
        ic_data = {
            'name': ic.name,
            'module_name': f'{module_prefix}_interconnect',
            'params': [],
            'connections': [],
            'initiators': [],
            'targets': [],
        }

        # Parameters
        for pname, pval in ic.params.items():
            val = pval
            if isinstance(val, str) and val.startswith('$'):
                val = val[1:]
            ic_data['params'].append({'name': pname, 'value': val})

        # Fixed connections (clock, reset, scan, remap)
        for conn in ic.connections:
            signal = self._resolve_conn_to_wire(conn.conn)
            ic_data['connections'].append({
                'port': conn.port,
                'signal': signal,
            })

        # Add scanouthclk as unconnected
        ic_data['connections'].append({
            'port': 'sys_scanouthclk',
            'signal': None,
            'desc': 'unconnected',
        })

        # Build flat port list
        ic_ports = []

        for init in ic.initiators:
            ic_ports.append({'type': 'comment', 'text': f'{init.name} Master Port'})

            if self._is_passthrough_initiator(init.name):
                # Passthrough initiator — connect to module-level AHB target port
                module_iface = self._passthrough_initiators[init.name]
                # Find the module interface to check for excludes
                top_iface = self._find_top_interface(module_iface)
                top_iface_params = top_iface.params if top_iface else {}
                # Which module-boundary form did _expand_ahb_port emit for
                # this passthrough-initiator iface? The SAME shared
                # classifier decides both the module ready-port name here
                # and the boundary in _expand_ahb_port — they must not
                # drift. NOTE: this only affects the module-side net name
                # for the ready feedback. The interconnect instance itself
                # always exposes the full cmsdk initiator port set
                # (incl. `<init>_hready`), so every AHB_INITIATOR_SIGNALS
                # port must still be connected — excluded signals only
                # change which module port the interconnect ties to, never
                # whether the interconnect port is connected at all.
                #
                #   initiator boundary (no EXCLUDE hready, e.g.
                #     eth_ss_0/eth_ss_1): the module ready port is the
                #     bare AHB_INITIATOR_SIGNALS name `<iface>_hready`.
                #   target boundary (EXCLUDE hready, e.g. cpu_ss): the
                #     module ready port is the slave-form
                #     `<iface>_hreadyout`.
                #
                # Hardcoding `_hreadyout` here (the old behaviour) left
                # the initiator-boundary module's `<iface>_hready` output
                # undriven (the interconnect drove a dangling implicit
                # `<iface>_hreadyout` net), so any external master reading
                # that subsystem slave saw Z HREADY and hung.
                uses_init_boundary = passthrough_initiator_uses_initiator_boundary(
                    top_iface_params)
                hsel_gated = init.name in self._hsel_gated_passthrough_initiators()

                for sig, sig_dir, sig_w in AHB_INITIATOR_SIGNALS:
                    ic_port_name = f'{init.name}_{sig}'

                    if sig_dir == 'out':
                        # Initiator output (haddr, htrans, etc.) — comes from module port input
                        module_sig = f'{module_iface}_{sig}'
                        if sig == 'htrans' and hsel_gated:
                            # Target-boundary form (cpu_ss): the parent
                            # matrix output port may show NONSEQ with
                            # HSEL=0; take the HSEL-qualified copy. See
                            # _hsel_gated_passthrough_initiators.
                            module_sig = f'{init.name}_htrans_gated'
                    else:
                        # Initiator input (hrdata, hready, hresp) — goes to module port output
                        if sig == 'hready' and not uses_init_boundary:
                            # Target-boundary form (cpu_ss): module port
                            # is the slave `<iface>_hreadyout`.
                            module_sig = f'{module_iface}_hreadyout'
                        else:
                            # Initiator-boundary form (eth_ss_0/eth_ss_1):
                            # module port is the bare `<iface>_hready`
                            # (also covers hrdata/hresp in both forms).
                            module_sig = f'{module_iface}_{sig}'

                    ic_ports.append({
                        'type': 'port',
                        'port': ic_port_name,
                        'signal': module_sig,
                    })
            else:
                # Internal initiator — use i_-prefixed wires
                for sig, sig_dir, sig_w in AHB_INITIATOR_SIGNALS:
                    ic_ports.append({
                        'type': 'port',
                        'port': f'{init.name}_{sig}',
                        'signal': f'i_{init.name}_{sig}',
                    })

        for tgt in ic.targets:
            ic_ports.append({'type': 'comment', 'text': f'{tgt.name} Target Port'})

            if self._is_passthrough_target(tgt.name):
                # Passthrough target — connect to module-level AHB initiator port
                module_iface = self._passthrough_targets[tgt.name]

                for sig, sig_dir, sig_w in AHB_TARGET_SIGNALS:
                    ic_port_name = f'{tgt.name}_{sig}'
                    if sig == 'hready':
                        ic_port_name = f'{tgt.name}_hreadymux'

                    if sig_dir == 'in':
                        # Target inputs (hsel, haddr, htrans, ...) are interconnect
                        # OUTPUTS. Drive the {tgt.name}_* intermediate wires (NOT the
                        # cpu_<n>_* ports directly); _build_passthrough_tieoffs then
                        # forwards them to the ports, gating HTRANS by system_hsel.
                        # Driving the wires keeps system_hsel/system_htrans driven so
                        # the gate has exactly one driver — an undriven system_htrans
                        # is Z in sim but 0 in synth, which collapses every CPU write
                        # to a shared peripheral to IDLE on FPGA.
                        if sig == 'hready':
                            # hreadymux is unused on passthrough
                            ic_ports.append({
                                'type': 'port',
                                'port': ic_port_name,
                                'signal': None,
                                'desc': 'unused output — system interconnect drives HREADY',
                            })
                            continue
                        else:
                            module_sig = f'{tgt.name}_{sig}'
                    else:
                        # Target output (hrdata, hresp, hreadyout) — comes from module port input
                        if sig == 'hreadyout':
                            module_sig = f'{module_iface}_hready'
                        else:
                            module_sig = f'{module_iface}_{sig}'

                    ic_ports.append({
                        'type': 'port',
                        'port': ic_port_name,
                        'signal': module_sig,
                    })
            else:
                # Internal target — use i_-prefixed wires
                for sig, sig_dir, sig_w in AHB_TARGET_SIGNALS:
                    if sig == 'hready':
                        ic_ports.append({
                            'type': 'port',
                            'port': f'{tgt.name}_hreadymux',
                            'signal': f'i_{tgt.name}_hreadymux',
                        })
                    else:
                        ic_ports.append({
                            'type': 'port',
                            'port': f'{tgt.name}_{sig}',
                            'signal': f'i_{tgt.name}_{sig}',
                        })

        ic_data['ports'] = ic_ports
        return ic_data

    @classmethod
    def generate_all(cls, top_module: Module, build_dir) -> List[Tuple[str, str]]:
        """Walk module hierarchy and generate SV for all subsystems with gen: True.

        Returns list of (module_name, output_path) tuples.
        """
        build_dir = Path(build_dir)
        rtl_dir = build_dir / 'rtl'
        flist_dir = build_dir / 'flist'
        results = []

        def _walk(module: Module):
            for inst in module.instances:
                if inst.resolved_module and inst.resolved_module.gen:
                    sub = inst.resolved_module
                    # Only generate if it has interconnects (structural subsystem)
                    if sub.interconnects or sub.instances:
                        backend = cls(sub)
                        out_path = backend.generate(rtl_dir, flist_dir)
                        if out_path:
                            results.append((sub.name, str(out_path)))
                    # Recurse into the subsystem
                    _walk(sub)

        _walk(top_module)
        return results
