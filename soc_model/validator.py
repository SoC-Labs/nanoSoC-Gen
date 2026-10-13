"""Validation of the SoC model: width matching, direction compatibility, address overlaps."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .adapters.base import resolve_boundary_port
from .adapters.registry import protocol_for
from .model import Connection, Instance, Interconnect, Interface, Module
from .utils import parse_bit_slice, parse_conn_ref, bit_slice_width
from .backends.protocol_utils import bus_member_names


@dataclass
class ValidationMessage:
    """A validation error or warning."""
    level: str  # 'error', 'warning', 'info'
    category: str  # 'width', 'direction', 'reference', 'address', 'connection'
    instance: str = ''
    port: str = ''
    message: str = ''

    def __str__(self):
        loc = f"{self.instance}.{self.port}" if self.instance else self.port
        return f"[{self.level.upper()}] ({self.category}) {loc}: {self.message}"


def waiver_key(msg: 'ValidationMessage') -> str:
    """`<category>:<location>` naming one validation error for
    --validation-waive: the location as the message prints it (instance.port,
    port, or instance)."""
    loc = (f"{msg.instance}.{msg.port}" if msg.port else msg.instance) if msg.instance else msg.port
    return f"{msg.category}:{loc}"


def parse_validation_waivers(items) -> Dict[str, str]:
    """`<category>:<location>=<reason>` strings -> {key: reason}. A waiver
    without a category, a location or a reason, or given twice, is a ValueError."""
    import re
    out: Dict[str, str] = {}
    for it in items or ():
        key, eq, reason = str(it).partition('=')
        key, reason = key.strip(), reason.strip()
        if not eq or not reason or not re.fullmatch(r'[a-z_]+:[A-Za-z0-9_$.\[\]:]+', key):
            raise ValueError(f"--validation-waive {it!r}: expected <category>:<location>=<reason> "
                             f"(as the error prints it, e.g. connection:u_x.PORT=why), with a reason")
        if key in out:
            raise ValueError(f"--validation-waive {key}: given twice")
        out[key] = reason
    return out


def _hand_written_member(module: Module, port_name: str) -> bool:
    """Is `port_name` a bundle member of one of `module`'s protocol ifaces, as
    hand-written RTL spells it (adapters `Protocol.hand_written_member`)?"""
    for iface in module.interfaces:
        proto = protocol_for(iface)
        if proto is not None and proto.hand_written_member(iface, port_name):
            return True
    return False


class SoCValidator:
    """Validates the SoC model for connectivity correctness."""

    def __init__(self, top_module: Module):
        self.top = top_module
        self.messages: List[ValidationMessage] = []

    def validate_all(self) -> List[ValidationMessage]:
        """Run all validation checks."""
        self.messages = []
        self._validate_connections(self.top)
        self._validate_interconnects(self.top)
        self._validate_address_overlaps(self.top)
        self._validate_instance_references(self.top)
        self._validate_known_rtl_modules(self.top)
        self._validate_driver_coverage(self.top)
        self._validate_xref_references(self.top)
        self._validate_interconnect_impl(self.top)
        return self.messages

    @property
    def errors(self) -> List[ValidationMessage]:
        return [m for m in self.messages if m.level == 'error']

    @property
    def warnings(self) -> List[ValidationMessage]:
        return [m for m in self.messages if m.level == 'warning']

    def _validate_connections(self, module: Module):
        """Validate all connections on all instances in a module."""
        for inst in module.instances:
            child_module = inst.resolved_module
            if not child_module:
                self.messages.append(ValidationMessage(
                    level='warning',
                    category='reference',
                    instance=inst.instance_name,
                    message=f"Unresolved module '{inst.module_name}'",
                ))
                continue

            # Build port lookup for the child module. Bus-typed interfaces
            # (ahb / dbg_ahb / swd / gpio / axis*) are flattened into per-signal
            # ports at the module boundary by the generator, so a connection may
            # legitimately target an expanded member (e.g. `cpu_0_dbgahb_slvaddr`
            # for the `cpu_0_dbgahb` dbg_ahb bus) that is not itself an entry in
            # `Module.interfaces`. Recognise those so we don't false-warn.
            child_ports = {iface.name: iface for iface in child_module.interfaces}
            bus_member_ports = set()
            for iface in child_module.interfaces:
                bus_member_ports.update(bus_member_names(iface))
            # A gen: False module is hand-written RTL: its member ports follow
            # the RTL's spelling (GEN-BACKLOG 20), recognised separately from
            # the generator's exact names, which gen: True modules keep.
            hand_written = not child_module.gen

            for conn in inst.connections:
                port_name, port_high, port_low = parse_bit_slice(conn.port)

                # Check port exists on child module
                child_iface = child_ports.get(port_name)
                if not child_iface:
                    if port_name in bus_member_ports:
                        # Valid expanded bus-member port. Width/direction are not
                        # readily available per-member here, so skip the deep
                        # connection check (status quo for bus members).
                        continue
                    if hand_written and _hand_written_member(child_module, port_name):
                        continue
                    # An ERROR (GEN-BACKLOG 23): this was a warning with exit
                    # 0, so a reversed --lib-dir order (arch_tech's SWD-less
                    # nanosoc_ss_cpu shadowing the project's) rendered a SoC
                    # without SWD and nothing failed. Bundle members are
                    # recognised above, so they never reach this error.
                    self.messages.append(ValidationMessage(
                        level='error',
                        category='reference',
                        instance=inst.instance_name,
                        port=conn.port,
                        message=(f"Port '{port_name}' not found on module '{child_module.name}': "
                                 f"the connection would go nowhere. A misspelt port, or the module "
                                 f"resolved from the wrong --lib-dir (the LAST lib dir that has a "
                                 f"module wins: list arch_tech's sys_desc first, the project's last)"),
                    ))
                    continue

                # Resolve the connection target
                self._validate_single_connection(module, inst, conn, child_iface, port_high, port_low)

    def _validate_single_connection(
        self,
        parent: Module,
        inst: Instance,
        conn: Connection,
        child_iface: Interface,
        port_high: Optional[int],
        port_low: Optional[int],
    ):
        """Validate a single port-to-signal connection."""
        conn_inst, conn_port, conn_high, conn_low = parse_conn_ref(conn.conn)

        # --- Width validation ---
        self._check_width(inst, conn, child_iface, port_high, port_low,
                          parent, conn_inst, conn_port, conn_high, conn_low)

        # --- Direction validation ---
        self._check_direction(inst, conn, child_iface, parent, conn_inst, conn_port)

    def _check_width(
        self,
        inst: Instance,
        conn: Connection,
        child_iface: Interface,
        port_high: Optional[int],
        port_low: Optional[int],
        parent: Module,
        conn_inst: Optional[str],
        conn_port: str,
        conn_high: Optional[int],
        conn_low: Optional[int],
    ):
        """Check that widths match on both sides of a connection."""
        # Get port side width
        port_width = self._get_effective_width(child_iface, port_high, port_low)
        if port_width is None:
            return  # can't validate without width info

        # Get connection side width
        conn_width = self._resolve_conn_width(
            parent, conn_inst, conn_port, conn_high, conn_low
        )

        if conn_width is not None and port_width != conn_width:
            self.messages.append(ValidationMessage(
                level='error',
                category='width',
                instance=inst.instance_name,
                port=conn.port,
                message=(
                    f"Width mismatch: port '{conn.port}' has width {port_width}, "
                    f"but conn '{conn.conn}' has width {conn_width}"
                ),
            ))

    def _check_direction(
        self,
        inst: Instance,
        conn: Connection,
        child_iface: Interface,
        parent: Module,
        conn_inst: Optional[str],
        conn_port: str,
    ):
        """Check that port directions are compatible."""
        if child_iface.is_bidirectional:
            return  # inout always compatible

        # If connecting to another instance's port, check directions are opposite
        if conn_inst:
            other_inst = parent.get_instance(conn_inst)
            if other_inst and other_inst.resolved_module:
                other_iface = other_inst.resolved_module.get_interface(conn_port)
                if other_iface:
                    if not other_iface.is_bidirectional:
                        # For bus protocols, initiator connects to target
                        if child_iface.type in ('ahb', 'apb', 'axis', 'axis_byte'):
                            self._check_bus_direction(inst, conn, child_iface, other_iface)
                        else:
                            # For wires: output must connect to input
                            if child_iface.is_output and other_iface.is_output:
                                self.messages.append(ValidationMessage(
                                    level='error',
                                    category='direction',
                                    instance=inst.instance_name,
                                    port=conn.port,
                                    message=(
                                        f"Direction conflict: port '{conn.port}' (output) "
                                        f"connects to '{conn.conn}' (also output)"
                                    ),
                                ))
                            elif child_iface.is_input and other_iface.is_input:
                                self.messages.append(ValidationMessage(
                                    level='error',
                                    category='direction',
                                    instance=inst.instance_name,
                                    port=conn.port,
                                    message=(
                                        f"Direction conflict: port '{conn.port}' (input) "
                                        f"connects to '{conn.conn}' (also input)"
                                    ),
                                ))

        # If connecting to parent module port, check directions match
        elif not conn_inst:
            parent_iface = parent.get_interface(conn_port)
            if parent_iface:
                # Child input should connect to parent input (pass-through)
                # Child output should connect to parent output (pass-through)
                # This is correct — no error needed
                pass

    def _check_bus_direction(
        self,
        inst: Instance,
        conn: Connection,
        child_iface: Interface,
        other_iface: Interface,
    ):
        """Check bus protocol direction compatibility."""
        child_dir = child_iface.direction
        other_dir = other_iface.direction

        # initiator <-> target is correct
        # out <-> in is correct for streams
        compatible_pairs = {
            ('initiator', 'target'), ('target', 'initiator'),
            ('out', 'in'), ('in', 'out'),
            ('sender', 'receiver'), ('receiver', 'sender'),
        }

        if (child_dir, other_dir) not in compatible_pairs:
            # Only warn, not error — some connections are indirect through interconnect
            self.messages.append(ValidationMessage(
                level='warning',
                category='direction',
                instance=inst.instance_name,
                port=conn.port,
                message=(
                    f"Bus direction check: '{conn.port}' ({child_dir}) "
                    f"connects to '{conn.conn}' ({other_dir})"
                ),
            ))

    def _get_effective_width(
        self,
        iface: Interface,
        high: Optional[int],
        low: Optional[int],
    ) -> Optional[int]:
        """Get effective width of an interface, accounting for bit slicing."""
        if high is not None and low is not None:
            return bit_slice_width(high, low)

        if iface.type == 'wire':
            return iface.width
        # Bus interfaces have multiple signals — width check is per-signal
        # For bus types, we check data_width and addr_width separately
        return None

    def _resolve_conn_width(
        self,
        parent: Module,
        conn_inst: Optional[str],
        conn_port: str,
        high: Optional[int],
        low: Optional[int],
    ) -> Optional[int]:
        """Resolve the width of a connection target."""
        if high is not None and low is not None:
            return bit_slice_width(high, low)

        # Check if it's a parent interface
        parent_iface = parent.get_interface(conn_port)
        if parent_iface and parent_iface.type == 'wire':
            return parent_iface.width

        # Check if it's another instance's port
        if conn_inst:
            other_inst = parent.get_instance(conn_inst)
            if other_inst and other_inst.resolved_module:
                other_iface = other_inst.resolved_module.get_interface(conn_port)
                if other_iface and other_iface.type == 'wire':
                    return other_iface.width

        return None  # can't determine width

    def _validate_interconnects(self, module: Module):
        """Validate interconnect target/initiator consistency."""
        for ic in module.interconnects:
            target_names = {t.name for t in ic.targets}

            for init in ic.initiators:
                for tgt_name in init.target_names:
                    if tgt_name not in target_names:
                        self.messages.append(ValidationMessage(
                            level='error',
                            category='reference',
                            instance=ic.name,
                            port=init.name,
                            message=(
                                f"Initiator '{init.name}' references target "
                                f"'{tgt_name}' which is not defined in targets"
                            ),
                        ))

    def _validate_interconnect_impl(self, module: Module, _seen=None):
        """Check each interconnect's ``impl`` and, for ``cmsdk_mux``, that the
        derived topology can be built. Walks the whole hierarchy: a
        subsystem's interconnect is generated too, so it is checked too.

        An unknown interconnect key is an error when it reads as a misspelt
        ``impl`` (or its value is ``cmsdk_mux``), and a warning on a
        cmsdk_mux interconnect otherwise. On a bus-matrix interconnect an
        ordinary unknown key adds no message, so bus-matrix reports (which
        embed these messages) do not change."""
        from .backends.cmsdk_mux import (check_cmsdk_mux, check_interconnect_keys,
                                         ic_impl)
        seen = _seen if _seen is not None else set()
        if id(module) in seen:
            return
        seen.add(id(module))
        for ic in module.interconnects:
            if not ic.gen:
                continue
            for reason in check_cmsdk_mux(ic, module.flat_params):
                self.messages.append(ValidationMessage(
                    level='error', category='interconnect',
                    instance=ic.name, message=reason,
                ))
            if ic_impl(ic) == 'cmsdk_mux':
                for note in check_interconnect_keys(ic)[1]:
                    self.messages.append(ValidationMessage(
                        level='warning', category='interconnect',
                        instance=ic.name, message=note,
                    ))
        for inst in module.instances:
            if inst.resolved_module:
                self._validate_interconnect_impl(inst.resolved_module, seen)

    def _validate_address_overlaps(self, module: Module):
        """Check for overlapping address ranges in interconnects."""
        for ic in module.interconnects:
            targets = sorted(ic.targets, key=lambda t: t.base)

            for i in range(len(targets) - 1):
                t1 = targets[i]
                t2 = targets[i + 1]
                t1_end = t1.base + t1.size
                if t1_end > t2.base:
                    self.messages.append(ValidationMessage(
                        level='error',
                        category='address',
                        instance=ic.name,
                        message=(
                            f"Address overlap: '{t1.name}' "
                            f"(0x{t1.base:08X}-0x{t1_end - 1:08X}) overlaps with "
                            f"'{t2.name}' (0x{t2.base:08X}-0x{t2.base + t2.size - 1:08X})"
                        ),
                    ))

    def _validate_instance_references(self, module: Module):
        """Check that all instance module references can be resolved."""
        for inst in module.instances:
            if not inst.resolved_module and not inst.is_rtl_module:
                self.messages.append(ValidationMessage(
                    level='error',
                    category='reference',
                    instance=inst.instance_name,
                    message=f"Module '{inst.module_name}' not found",
                ))

    def _validate_driver_coverage(self, module: Module):
        """Flag top-level wire OUTPUT ports that have no driver, and declared
        top-level wire ports that are never referenced anywhere.

        Catches the 'dangling output' class of bug: a top-level output port is
        declared, but the instance output that should feed it is instead tied
        to a throwaway internal net (commonly named `*_nc`), leaving the port
        undriven — X in simulation, a constant/floating value in synthesis.
        See nanosoc-multicore-system: cpu_1_txev/lockup/sysresetreq/sleeping/
        sleepdeep were declared but driven nowhere.

        Scope: only `type == 'wire'` ports. Bus-typed ports (ahb/apb/axis/...)
        are expanded into per-signal ports by the backend and their driver
        analysis lives in the protocol layer, so they are out of scope here.
        """
        def _flat_net(ref: str):
            """Flat net base name for a conn string, or None for a
            hierarchical `instance.port` reference (not a flat net)."""
            conn_inst, conn_port, _, _ = parse_conn_ref(ref)
            if conn_inst is not None:
                return None
            base, _, _ = parse_bit_slice(conn_port)
            return base

        driven = set()      # flat nets that have a driver (for C1)
        referenced = set()  # flat nets referenced anywhere (for C2)

        # Instance connections: an instance OUTPUT port drives its conn net;
        # every connection (regardless of direction) references its conn net.
        for inst in module.instances:
            child = inst.resolved_module
            child_ports = {i.name: i for i in child.interfaces} if child else {}
            for conn in inst.connections:
                if conn.is_unconnected:
                    continue
                net = _flat_net(conn.conn)
                if net is None:
                    continue
                referenced.add(net)
                port_name, _, _ = parse_bit_slice(conn.port)
                child_iface = child_ports.get(port_name)
                if child_iface is not None and child_iface.is_output:
                    driven.add(net)

        # Glue logic: the output net is driven; input nets are referenced.
        for g in module.glue_logic:
            if g.output:
                ob, _, _ = parse_bit_slice(g.output)
                driven.add(ob)
                referenced.add(ob)
            srcs = ([g.input] if g.input else []) + list(g.inputs or [])
            for src in srcs:
                sb = _flat_net(src)
                if sb:
                    referenced.add(sb)
            for ci in (g.concat_inputs or []):
                sig = ci.get('signal') if isinstance(ci, dict) else None
                if sig:
                    sb = _flat_net(sig)
                    if sb:
                        referenced.add(sb)

        # Legacy combinational assigns: target driven, also referenced.
        for a in module.assigns:
            if a.target:
                tb, _, _ = parse_bit_slice(a.target)
                driven.add(tb)
                referenced.add(tb)

        # Interconnect connections reference their conn nets.
        for ic in module.interconnects:
            for conn in ic.connections:
                if conn.is_unconnected:
                    continue
                net = _flat_net(conn.conn)
                if net:
                    referenced.add(net)

        # C1 — top-level wire OUTPUT with no driver.
        c1_flagged = set()
        for iface in module.interfaces:
            if iface.type != 'wire' or not iface.is_output:
                continue
            if iface.name not in driven:
                c1_flagged.add(iface.name)
                self.messages.append(ValidationMessage(
                    level='error',
                    category='connection',
                    port=iface.name,
                    message=(
                        f"Top-level output '{iface.name}' has no driver — no "
                        f"instance output, glue-logic output, or assign targets "
                        f"it. (Often the driving instance output was tied to a "
                        f"throwaway _nc net instead of this port.)"
                    ),
                ))

        # C2 — declared top-level wire port never referenced anywhere. Skip
        # ports already reported by C1 to avoid double-reporting.
        for iface in module.interfaces:
            if iface.type != 'wire' or iface.name in c1_flagged:
                continue
            if iface.name not in referenced:
                self.messages.append(ValidationMessage(
                    level='warning',
                    category='connection',
                    port=iface.name,
                    message=(
                        f"Top-level port '{iface.name}' is declared but never "
                        f"referenced by any connection, glue, or assign."
                    ),
                ))

    def _validate_xref_references(self, module: Module):
        """Cross-instance `conn: <inst>.<port>` references must RESOLVE on the
        referenced instance: to a declared interface, an expanded bus/stream
        member port (via the same adapters.resolve_boundary_port matcher the
        top-level binder uses), or a port the referenced instance itself
        connects explicitly (the binder resolves through that connection).

        Anything else used to generate RTL with a silently severed net — the
        binder declared a flat wire, the referencing side bound to it, and the
        referenced side never did. Downstream (nanosoc_m0_soc PYNQ-Z2
        bring-up) that made an APB completer's PREADY float and hung the
        AHB→APB bridge on the first UART write. Loud error > broken silicon.

        Whole-bundle cross-references (u_x.<bus iface>) are rejected too: a
        protocol bundle is not a single RTL port, so `.IFACE(wire)` cannot be
        emitted meaningfully — bind per member or through named wires.
        """
        ic_names = {ic.name for ic in module.interconnects}

        def _check(ref: str, where_instance: str, where_port: str):
            conn_inst, conn_port, _, _ = parse_conn_ref(ref)
            if conn_inst is None or conn_inst in ic_names:
                return
            other = module.get_instance(conn_inst)
            if other is None:
                self.messages.append(ValidationMessage(
                    level='error',
                    category='connection',
                    instance=where_instance,
                    port=where_port,
                    message=(
                        f"Connection '{ref}' references unknown instance "
                        f"'{conn_inst}'"
                    ),
                ))
                return
            ifaces = []
            if other.resolved_module is not None:
                ifaces.extend(other.resolved_module.interfaces)
            ifaces.extend(other.inline_interfaces)
            if not ifaces and other.resolved_module is None:
                return  # unresolved module: already warned in _validate_connections
            hit = resolve_boundary_port(ifaces, conn_port)
            if hit is not None:
                iface, member = hit
                if member is None and protocol_for(iface) is not None:
                    self.messages.append(ValidationMessage(
                        level='error',
                        category='connection',
                        instance=where_instance,
                        port=where_port,
                        message=(
                            f"Connection '{ref}' references the whole "
                            f"'{iface.type}' bundle '{conn_port}' on "
                            f"'{conn_inst}' — a protocol bundle is not a "
                            f"single RTL port. Bind each member "
                            f"(e.g. {conn_port}_<signal>) or route the link "
                            f"through named internal wires bound on both "
                            f"endpoints."
                        ),
                    ))
                return
            # Not declared: OK when the referenced instance explicitly
            # connects the port (the binder resolves through that conn).
            for c in other.connections:
                if c.is_unconnected:
                    continue
                c_base, _, _ = parse_bit_slice(c.port)
                if c_base == conn_port:
                    return
            self.messages.append(ValidationMessage(
                level='error',
                category='connection',
                instance=where_instance,
                port=where_port,
                message=(
                    f"Connection '{ref}' does not resolve on instance "
                    f"'{conn_inst}' (module "
                    f"'{other.resolved_module.name if other.resolved_module else other.module_name}'): "
                    f"'{conn_port}' is not a declared interface, not a "
                    f"recognisable bus-member port, and the instance never "
                    f"connects it. The generated RTL would leave this net "
                    f"silently severed (declared flat wire bound on one end "
                    f"only). Declare the port in the module YAML, use the "
                    f"canonical <iface>_<member> spelling, or bind both "
                    f"endpoints through a named internal wire."
                ),
            ))

        for inst in module.instances:
            for conn in inst.connections:
                if conn.is_unconnected:
                    continue
                _check(conn.conn, inst.instance_name, conn.port)

        for gl in module.glue_logic:
            srcs = ([gl.input] if gl.input else []) + list(gl.inputs or [])
            for ci in (gl.concat_inputs or []):
                if isinstance(ci, dict) and ci.get('signal'):
                    srcs.append(str(ci['signal']))
            for src in srcs:
                _check(src, f'glue:{gl.name}', gl.output or '')

    # Known-pitfall port sets for bare `rtl_module:` instances. A `cmsdk_ahb_to_apb`
    # bridge declared without its APB-side *inputs* (PCLKEN / PREADY / PSLVERR)
    # will elaborate successfully but hang on the first APB transaction because
    # those inputs float to X in sim and Z on FPGA. Only inputs can hang the bus:
    # PSTRB is a bridge *output* (byte-lane strobe), so leaving it unconnected
    # never freezes the FSM — a consumer that needs strobes is a separate,
    # functional concern, not this guard's. The YAML is authoritative, so we
    # catch the floating-input pitfall at validate time rather than guess-inject.
    _REQUIRED_RTL_PORTS = {
        'cmsdk_ahb_to_apb': {'PCLKEN', 'PREADY', 'PSLVERR'},
    }

    def _validate_known_rtl_modules(self, module: Module):
        """For bare rtl_module: instances of modules with known-pitfall ports,
        require the user to declare the port in the instance's interfaces
        list. Missing these has produced hung APB buses on two peripheral
        bridges in downstream SoCs; a clear error saves the debug."""
        for inst in module.instances:
            if not inst.is_rtl_module:
                continue
            required = self._REQUIRED_RTL_PORTS.get(inst.module_name)
            if not required:
                continue
            declared = {iface.name.upper() for iface in inst.inline_interfaces}
            missing = sorted(required - declared)
            if missing:
                self.messages.append(ValidationMessage(
                    level='error',
                    category='connection',
                    instance=inst.instance_name,
                    message=(
                        f"rtl_module '{inst.module_name}' instance is missing "
                        f"required port declarations: {', '.join(missing)}. "
                        f"Declare each as an `interfaces:` entry and tie in "
                        f"`connections:` (e.g. PCLKEN → 1'b1 when PCLK==HCLK). "
                        f"Omitting these leaves the APB bus hung in sim."
                    ),
                ))
