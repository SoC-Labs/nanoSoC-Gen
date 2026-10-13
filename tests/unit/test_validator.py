# Copyright 2026, SoC Labs (www.soclabs.org)
"""Unit tests for soc_model/validator.py — SoCValidator and ValidationMessage.

Strategy (per TEST_PLAN.md 5.4): build malformed Modules inline from
dataclasses. SoCValidator.validate_all iterates over six independent list
fields of the top Module (instances / interconnects / glue_logic / assigns /
interfaces), so populating exactly one keeps the other five checks silent —
each test class can target a single rule in isolation.

Double-emission trap: an Instance with resolved_module=None fires BOTH a
_validate_connections "Unresolved module" warning AND a
_validate_instance_references "not found" error. Where a test wants to
isolate the connection/driver path, resolved_module is set explicitly to a
stub child Module so neither of those fires.
"""

import pytest

from soc_model.model import (
    Assign,
    Connection,
    GlueLogicEntry,
    Instance,
    Interconnect,
    InterconnectInitiator,
    InterconnectInitiatorTarget,
    InterconnectTarget,
    Interface,
    Module,
)
from soc_model.validator import SoCValidator, ValidationMessage

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Small construction helpers (kept local; no shared-file edits).
# ---------------------------------------------------------------------------

def _wire(name, direction='in', width=None):
    """A wire interface, optionally with an integer WIDTH param."""
    params = {} if width is None else {'WIDTH': width}
    return Interface(name=name, type='wire', direction=direction, params=params)


def _validate(top):
    """Run validate_all and return the message list."""
    return SoCValidator(top).validate_all()


def _child(name='child', interfaces=None):
    """A resolved child Module used to satisfy resolved_module."""
    return Module(name=name, interfaces=list(interfaces or []))


def _inst(instance_name='u', module_name='child', child=None, connections=None,
          is_rtl=False, inline=None):
    """An Instance, with resolved_module pre-attached to `child` unless None."""
    return Instance(
        instance_name=instance_name,
        module_name=module_name,
        is_rtl_module=is_rtl,
        connections=list(connections or []),
        inline_interfaces=list(inline or []),
        resolved_module=child,
    )


# ===========================================================================
# ValidationMessage dataclass (V-VM-*)
# ===========================================================================

class TestValidationMessage:

    def test_str_with_instance(self):  # V-VM-01
        m = ValidationMessage('warning', 'reference', 'u', 'p', 'msg')
        assert str(m) == "[WARNING] (reference) u.p: msg"

    def test_str_without_instance_bare_port(self):  # V-VM-02
        m = ValidationMessage('error', 'connection', port='o', message='m')
        assert str(m) == "[ERROR] (connection) o: m"

    def test_default_fields_and_level_upper(self):  # V-VM-03
        m = ValidationMessage('info', 'x')
        assert m.instance == ''
        assert m.port == ''
        assert m.message == ''
        assert str(m).startswith("[INFO] (x)")

    def test_fields_stored_verbatim(self):
        m = ValidationMessage(level='error', category='width', instance='i',
                              port='p', message='boom')
        assert (m.level, m.category, m.instance, m.port, m.message) == (
            'error', 'width', 'i', 'p', 'boom')


# ===========================================================================
# errors / warnings properties + validate_all reset (V-PR-*, V-VA-*)
# ===========================================================================

class TestProperties:

    def _mixed_model(self):
        """A top whose driver-coverage check yields exactly 1 error (C1,
        undriven output) + 1 warning (C2, unreferenced input). See V-DC-15."""
        top = Module('t', interfaces=[
            _wire('o', 'out'),   # C1: undriven output -> error
            _wire('din', 'in'),  # C2: unreferenced port -> warning
        ])
        return top

    def test_errors_filters_error_level(self):  # V-PR-01
        v = SoCValidator(self._mixed_model())
        v.validate_all()
        assert len(v.errors) == 1
        assert all(m.level == 'error' for m in v.errors)

    def test_warnings_filters_warning_level(self):  # V-PR-02
        v = SoCValidator(self._mixed_model())
        v.validate_all()
        assert len(v.warnings) == 1
        assert all(m.level == 'warning' for m in v.warnings)

    def test_empty_model_no_messages(self):  # V-PR-03
        v = SoCValidator(Module('t'))
        assert v.validate_all() == []
        assert v.errors == []
        assert v.warnings == []

    def test_validate_all_is_idempotent_resets(self):  # V-VA-01
        v = SoCValidator(Module('t'))
        first = v.validate_all()
        second = v.validate_all()
        assert first == []
        assert second == []
        assert len(v.messages) == 0


# ===========================================================================
# _validate_connections — reference / port-existence (V-C-*)
# ===========================================================================

class TestConnectionReferences:

    def test_unresolved_child_warns_and_continues(self):  # V-C-01
        # resolved_module=None -> "Unresolved module" warning. is_rtl False
        # also yields the instance-references "not found" error, so filter.
        top = Module('t', instances=[
            Instance(instance_name='u', module_name='nope'),
        ])
        msgs = _validate(top)
        refs = [m for m in msgs if m.level == 'warning' and m.category == 'reference']
        assert len(refs) == 1
        assert "Unresolved module 'nope'" in refs[0].message
        assert refs[0].instance == 'u'

    def test_port_not_on_child_is_an_error(self):  # V-C-02 (an error since GEN-BACKLOG 23)
        child = _child('c', [_wire('real', 'in')])
        top = Module('t', instances=[
            _inst('u', 'c', child, [Connection('ghost', 'sys_net')]),
        ])
        msgs = _validate(top)
        refs = [m for m in msgs if m.category == 'reference']
        assert len(refs) == 1
        assert refs[0].level == 'error'
        assert "Port 'ghost' not found on module 'c'" in refs[0].message
        assert refs[0].port == 'ghost'

    def test_bus_member_expanded_port_recognised(self):  # V-C-03
        # A dbg_ahb iface named cpu_0_dbgahb expands to cpu_0_dbgahb_slvaddr.
        child = _child('c', [Interface('cpu_0_dbgahb', type='dbg_ahb',
                                       direction='target')])
        top = Module('t', instances=[
            _inst('u', 'c', child,
                  [Connection('cpu_0_dbgahb_slvaddr', 'some_net')]),
        ])
        # No reference warning for the recognised member.
        msgs = _validate(top)
        assert msgs == []

    def test_bit_sliced_port_name_parsed_for_lookup(self):  # V-C-04
        # port "p[3:0]" must look up "p"; widths match (slice 4 == net 4).
        child = _child('c', [_wire('p', 'in', width=8)])
        top = Module('t',
                     interfaces=[_wire('netw4', 'in', width=4)],
                     instances=[
                         _inst('u', 'c', child,
                               [Connection('p[3:0]', 'netw4')]),
                     ])
        msgs = _validate(top)
        # "p" was found (no reference warning). Width: slice gives 4, parent
        # net is 4 -> no mismatch.
        assert not any(m.category == 'reference' for m in msgs)
        assert not any(m.category == 'width' for m in msgs)


# ===========================================================================
# _check_width (V-CW-*)
# ===========================================================================

class TestConnectionWidth:

    def test_wire_width_mismatch_errors(self):  # V-CW-01
        child = _child('c', [_wire('p', 'in', width=8)])
        top = Module('t',
                     interfaces=[_wire('net', 'in', width=4)],
                     instances=[
                         _inst('u', 'c', child, [Connection('p', 'net')]),
                     ])
        errs = [m for m in _validate(top) if m.category == 'width']
        assert len(errs) == 1
        assert errs[0].level == 'error'
        assert "width 8" in errs[0].message and "width 4" in errs[0].message
        assert errs[0].instance == 'u'
        assert errs[0].port == 'p'

    def test_bus_port_no_slice_skips_width(self):  # V-CW-02
        # An ahb (bus) iface with no slice -> _get_effective_width None -> skip.
        child = _child('c', [Interface('m', type='ahb', direction='initiator')])
        top = Module('t',
                     interfaces=[_wire('net', 'in', width=4)],
                     instances=[
                         _inst('u', 'c', child, [Connection('m', 'net')]),
                     ])
        assert not any(m.category == 'width' for m in _validate(top))

    def test_conn_width_unknown_skips_width(self):  # V-CW-03
        # Port is a real wire (width 8), but conn target is unknown (no parent
        # iface, no instance) -> conn_width None -> final `return None` -> skip.
        child = _child('c', [_wire('p', 'in', width=8)])
        top = Module('t', instances=[
            _inst('u', 'c', child, [Connection('p', 'mystery_net')]),
        ])
        assert not any(m.category == 'width' for m in _validate(top))

    def test_conn_width_to_ahb_parent_skips_width(self):  # V-CW-03 (parent ahb)
        # Parent iface exists but is non-wire (ahb) -> conn width None -> skip.
        child = _child('c', [_wire('p', 'in', width=8)])
        top = Module('t',
                     interfaces=[Interface('busport', type='ahb',
                                           direction='target')],
                     instances=[
                         _inst('u', 'c', child, [Connection('p', 'busport')]),
                     ])
        assert not any(m.category == 'width' for m in _validate(top))

    def test_conn_side_bit_slice_gives_width_match(self):  # V-CW-04
        # conn "bus[3:0]" -> width 4 from the slice; port wire width 4 -> ok.
        child = _child('c', [_wire('p', 'in', width=4)])
        top = Module('t', instances=[
            _inst('u', 'c', child, [Connection('p', 'bus[3:0]')]),
        ])
        assert not any(m.category == 'width' for m in _validate(top))

    def test_conn_side_bit_slice_mismatch_errors(self):  # V-CW-04 negative
        child = _child('c', [_wire('p', 'in', width=8)])
        top = Module('t', instances=[
            _inst('u', 'c', child, [Connection('p', 'bus[3:0]')]),
        ])
        errs = [m for m in _validate(top) if m.category == 'width']
        assert len(errs) == 1
        assert "width 8" in errs[0].message and "width 4" in errs[0].message

    def test_conn_other_instance_wire_port_width(self):  # V-CW-05
        # conn references v.q where v's child iface q is a wire of width 8.
        child_u = _child('cu', [_wire('p', 'in', width=8)])
        child_v = _child('cv', [_wire('q', 'out', width=8)])
        top = Module('t', instances=[
            _inst('u', 'cu', child_u, [Connection('p', 'v.q')]),
            _inst('v', 'cv', child_v),
        ])
        assert not any(m.category == 'width' for m in _validate(top))

    def test_conn_other_instance_wire_port_width_mismatch(self):  # V-CW-05 neg
        child_u = _child('cu', [_wire('p', 'in', width=8)])
        child_v = _child('cv', [_wire('q', 'out', width=4)])
        top = Module('t', instances=[
            _inst('u', 'cu', child_u, [Connection('p', 'v.q')]),
            _inst('v', 'cv', child_v),
        ])
        errs = [m for m in _validate(top) if m.category == 'width']
        assert len(errs) == 1
        assert "width 8" in errs[0].message and "width 4" in errs[0].message


# ===========================================================================
# _check_direction / _check_bus_direction (V-CD-*)
# ===========================================================================

class TestConnectionDirection:

    def test_wire_output_to_output_errors(self):  # V-CD-01
        child_a = _child('ca', [_wire('o', 'out')])
        child_b = _child('cb', [_wire('o', 'out')])
        top = Module('t', instances=[
            _inst('a', 'ca', child_a, [Connection('o', 'b.o')]),
            _inst('b', 'cb', child_b),
        ])
        errs = [m for m in _validate(top) if m.category == 'direction']
        assert len(errs) == 1
        assert errs[0].level == 'error'
        assert "(output)" in errs[0].message and "also output" in errs[0].message

    def test_wire_input_to_input_errors(self):  # V-CD-02
        child_a = _child('ca', [_wire('d', 'in')])
        child_b = _child('cb', [_wire('d', 'in')])
        top = Module('t', instances=[
            _inst('a', 'ca', child_a, [Connection('d', 'b.d')]),
            _inst('b', 'cb', child_b),
        ])
        errs = [m for m in _validate(top) if m.category == 'direction']
        assert len(errs) == 1
        assert "(input)" in errs[0].message and "also input" in errs[0].message

    def test_bidirectional_inout_skipped(self):  # V-CD-03
        child_a = _child('ca', [Interface('io', type='wire', direction='inout')])
        child_b = _child('cb', [Interface('io', type='wire', direction='inout')])
        top = Module('t', instances=[
            _inst('a', 'ca', child_a, [Connection('io', 'b.io')]),
            _inst('b', 'cb', child_b),
        ])
        assert not any(m.category == 'direction' for m in _validate(top))

    def test_conn_to_parent_port_no_error(self):  # V-CD-04
        # child clk (in) connects to a parent input port -> pass-through, ok.
        child = _child('c', [_wire('clk', 'in')])
        top = Module('t',
                     interfaces=[_wire('sys_clk', 'in')],
                     instances=[
                         _inst('u', 'c', child, [Connection('clk', 'sys_clk')]),
                     ])
        assert not any(m.category == 'direction' for m in _validate(top))

    def test_conn_inst_names_missing_instance_no_dir_error(self):  # V-CD-05
        child = _child('c', [_wire('o', 'out')])
        top = Module('t', instances=[
            _inst('u', 'c', child, [Connection('o', 'missing.o')]),
        ])
        # 'missing' instance does not exist -> other_inst None -> no dir check.
        assert not any(m.category == 'direction' for m in _validate(top))

    def test_bus_incompatible_initiator_to_initiator_warns(self):  # V-CD-06
        child_a = _child('ca', [Interface('m', type='ahb', direction='initiator')])
        child_b = _child('cb', [Interface('m', type='ahb', direction='initiator')])
        top = Module('t', instances=[
            _inst('a', 'ca', child_a, [Connection('m', 'b.m')]),
            _inst('b', 'cb', child_b),
        ])
        dirs = [m for m in _validate(top) if m.category == 'direction']
        assert len(dirs) == 1
        assert dirs[0].level == 'warning'      # WARNING, not error
        assert "Bus direction check" in dirs[0].message

    @pytest.mark.parametrize("d_a,d_b,btype", [
        ('initiator', 'target', 'ahb'),
        ('target', 'initiator', 'ahb'),
        ('out', 'in', 'axis'),
        ('in', 'out', 'axis'),
        ('sender', 'receiver', 'axis_byte'),
        ('receiver', 'sender', 'axis_byte'),
    ])
    def test_bus_compatible_pairs_no_message(self, d_a, d_b, btype):  # V-CD-07
        child_a = _child('ca', [Interface('m', type=btype, direction=d_a)])
        child_b = _child('cb', [Interface('m', type=btype, direction=d_b)])
        top = Module('t', instances=[
            _inst('a', 'ca', child_a, [Connection('m', 'b.m')]),
            _inst('b', 'cb', child_b),
        ])
        assert not any(m.category == 'direction' for m in _validate(top))

    def test_apb_routes_to_bus_check_not_wire_error(self):  # V-CD-08
        # apb is in the bus-dispatch set; incompatible dirs -> warning (bus),
        # NOT the wire output/output error path.
        child_a = _child('ca', [Interface('m', type='apb', direction='initiator')])
        child_b = _child('cb', [Interface('m', type='apb', direction='initiator')])
        top = Module('t', instances=[
            _inst('a', 'ca', child_a, [Connection('m', 'b.m')]),
            _inst('b', 'cb', child_b),
        ])
        dirs = [m for m in _validate(top) if m.category == 'direction']
        assert len(dirs) == 1
        assert dirs[0].level == 'warning'
        assert "Bus direction check" in dirs[0].message


# ===========================================================================
# _validate_interconnects (V-IC-*)
# ===========================================================================

class TestInterconnects:

    def test_initiator_references_undefined_target_errors(self):  # V-IC-01
        ic = Interconnect('ic',
            targets=[InterconnectTarget('t1')],
            initiators=[InterconnectInitiator('m', targets=[
                InterconnectInitiatorTarget('t1'),
                InterconnectInitiatorTarget('ghost'),
            ])])
        top = Module('t', interconnects=[ic])
        errs = [m for m in _validate(top) if m.category == 'reference']
        assert len(errs) == 1
        assert errs[0].level == 'error'
        assert "references target 'ghost' which is not defined" in errs[0].message
        assert errs[0].instance == 'ic'
        assert errs[0].port == 'm'

    def test_all_targets_defined_no_error(self):  # V-IC-02
        ic = Interconnect('ic',
            targets=[InterconnectTarget('t1'), InterconnectTarget('t2')],
            initiators=[InterconnectInitiator('m', targets=[
                InterconnectInitiatorTarget('t1'),
                InterconnectInitiatorTarget('t2'),
            ])])
        top = Module('t', interconnects=[ic])
        assert not any(m.category == 'reference' for m in _validate(top))

    def test_multiple_interconnects_one_bad_empty_targets_noop(self):  # V-IC-03
        good = Interconnect('good',
            targets=[InterconnectTarget('a')],
            initiators=[InterconnectInitiator('mg')])  # empty init targets
        bad = Interconnect('bad',
            targets=[InterconnectTarget('x')],
            initiators=[InterconnectInitiator('mb', targets=[
                InterconnectInitiatorTarget('nope'),
            ])])
        top = Module('t', interconnects=[good, bad])
        errs = [m for m in _validate(top) if m.category == 'reference']
        assert len(errs) == 1
        assert errs[0].instance == 'bad'


# ===========================================================================
# _validate_address_overlaps (V-AO-*)
# ===========================================================================

class TestAddressOverlaps:

    def _ic(self, *targets):
        return Module('t', interconnects=[
            Interconnect('ic', targets=list(targets))])

    def test_overlap_exact_message(self):  # V-AO-01
        top = self._ic(
            InterconnectTarget('a', base=0x0, size=0x1000),
            InterconnectTarget('b', base=0x800, size=0x1000),
        )
        errs = [m for m in _validate(top) if m.category == 'address']
        assert len(errs) == 1
        assert errs[0].level == 'error'
        assert errs[0].instance == 'ic'
        assert errs[0].message == (
            "Address overlap: 'a' (0x00000000-0x00000FFF) overlaps with "
            "'b' (0x00000800-0x000017FF)")

    def test_exact_adjacent_no_error_boundary(self):  # V-AO-02
        # t1_end == t2.base -> NOT > -> no overlap.
        top = self._ic(
            InterconnectTarget('a', base=0x0, size=0x1000),
            InterconnectTarget('b', base=0x1000, size=0x1000),
        )
        assert not any(m.category == 'address' for m in _validate(top))

    def test_one_byte_overlap_is_flagged(self):  # boundary +1
        # t1_end = 0x1001 > 0x1000 -> overlap.
        top = self._ic(
            InterconnectTarget('a', base=0x0, size=0x1001),
            InterconnectTarget('b', base=0x1000, size=0x1000),
        )
        assert len([m for m in _validate(top) if m.category == 'address']) == 1

    def test_sorted_by_base_before_pairwise(self):  # V-AO-03
        # Declared out of order: b first then a; still detected, t1=a.
        top = self._ic(
            InterconnectTarget('b', base=0x1000, size=0x800),
            InterconnectTarget('a', base=0x0, size=0x1800),
        )
        errs = [m for m in _validate(top) if m.category == 'address']
        assert len(errs) == 1
        assert "'a'" in errs[0].message
        assert errs[0].message.index("'a'") < errs[0].message.index("'b'")

    def test_single_target_no_error(self):  # V-AO-04 single
        top = self._ic(InterconnectTarget('only', base=0, size=0x1000))
        assert not any(m.category == 'address' for m in _validate(top))

    def test_empty_targets_no_error(self):  # V-AO-04 empty
        top = Module('t', interconnects=[Interconnect('ic')])
        assert not any(m.category == 'address' for m in _validate(top))

    def test_end_math_t1_end_minus_one_in_message(self):  # V-AO-05
        top = self._ic(
            InterconnectTarget('a', base=0x2000, size=0x100),
            InterconnectTarget('b', base=0x2080, size=0x100),
        )
        errs = [m for m in _validate(top) if m.category == 'address']
        assert len(errs) == 1
        assert "0x00002000-0x000020FF" in errs[0].message

    def test_three_targets_only_adjacent_pair_overlaps(self):  # V-AO-06
        top = self._ic(
            InterconnectTarget('a', base=0x0, size=0x1000),     # ends 0xFFF
            InterconnectTarget('b', base=0x1000, size=0x1000),  # ends 0x1FFF
            InterconnectTarget('c', base=0x1800, size=0x1000),  # overlaps b
        )
        errs = [m for m in _validate(top) if m.category == 'address']
        assert len(errs) == 1
        assert "'b'" in errs[0].message and "'c'" in errs[0].message


# ===========================================================================
# _validate_instance_references (V-IR-*)
# ===========================================================================

class TestInstanceReferences:

    def test_unresolved_not_rtl_errors(self):  # V-IR-01
        top = Module('t', instances=[
            Instance(instance_name='u', module_name='nope',
                     is_rtl_module=False, resolved_module=None),
        ])
        msgs = _validate(top)
        errs = [m for m in msgs if m.level == 'error' and m.category == 'reference']
        assert len(errs) == 1
        assert "Module 'nope' not found" in errs[0].message
        assert errs[0].instance == 'u'
        # The connections pass ALSO emits a warning for the same instance.
        assert any(m.level == 'warning' and m.category == 'reference'
                   for m in msgs)

    def test_rtl_module_true_no_not_found_error(self):  # V-IR-02
        top = Module('t', instances=[
            Instance(instance_name='u', module_name='some_rtl',
                     is_rtl_module=True, resolved_module=None),
        ])
        assert not any("not found" in m.message for m in _validate(top))

    def test_resolved_module_set_no_error(self):  # V-IR-03
        child = _child('c')
        top = Module('t', instances=[
            _inst('u', 'c', child),
        ])
        assert not any(m.category == 'reference' for m in _validate(top))


# ===========================================================================
# _validate_known_rtl_modules / _REQUIRED_RTL_PORTS (V-RTL-*)
# ===========================================================================

class TestKnownRtlModules:

    def _rtl_inst(self, inline_names):
        return Instance(
            instance_name='u_bridge',
            module_name='cmsdk_ahb_to_apb',
            is_rtl_module=True,
            inline_interfaces=[_wire(n) for n in inline_names],
        )

    def test_missing_pready_pslverr_errors_sorted(self):  # V-RTL-01
        top = Module('t', instances=[self._rtl_inst(['PCLKEN'])])
        errs = [m for m in _validate(top)
                if m.category == 'connection' and m.level == 'error']
        assert len(errs) == 1
        assert ("missing required port declarations: PREADY, PSLVERR"
                in errs[0].message)
        assert errs[0].instance == 'u_bridge'

    def test_declared_ports_case_insensitive(self):  # V-RTL-02
        top = Module('t', instances=[
            self._rtl_inst(['pclken', 'pready', 'pslverr'])])
        assert not any("missing required port" in m.message for m in _validate(top))

    def test_all_required_declared_no_error(self):  # V-RTL-03
        top = Module('t', instances=[
            self._rtl_inst(['PCLKEN', 'PREADY', 'PSLVERR'])])
        assert not any("missing required port" in m.message for m in _validate(top))

    def test_non_rtl_module_instance_skipped(self):  # V-RTL-04
        # Same module_name but is_rtl_module=False -> this guard skips it.
        top = Module('t', instances=[
            Instance(instance_name='u', module_name='cmsdk_ahb_to_apb',
                     is_rtl_module=False, resolved_module=_child('cmsdk'),
                     inline_interfaces=[]),
        ])
        assert not any("missing required port" in m.message for m in _validate(top))

    def test_module_name_not_in_dict_skipped(self):  # V-RTL-05
        top = Module('t', instances=[
            Instance(instance_name='u', module_name='cmsdk_apb_uart',
                     is_rtl_module=True, inline_interfaces=[]),
        ])
        assert not any("missing required port" in m.message for m in _validate(top))

    def test_missing_list_sorted_alphabetical(self):  # V-RTL-06
        # Declare only PSLVERR -> PCLKEN, PREADY remain (sorted).
        top = Module('t', instances=[self._rtl_inst(['PSLVERR'])])
        errs = [m for m in _validate(top) if m.category == 'connection']
        assert len(errs) == 1
        assert "missing required port declarations: PCLKEN, PREADY" in errs[0].message

    def test_empty_inline_all_three_missing(self):  # V-RTL-07
        top = Module('t', instances=[self._rtl_inst([])])
        errs = [m for m in _validate(top) if m.category == 'connection']
        assert len(errs) == 1
        assert ("missing required port declarations: PCLKEN, PREADY, PSLVERR"
                in errs[0].message)

    def test_required_ports_dict_contract(self):  # V-RTL-08
        assert SoCValidator._REQUIRED_RTL_PORTS == {
            'cmsdk_ahb_to_apb': {'PCLKEN', 'PREADY', 'PSLVERR'},
        }


# ===========================================================================
# _validate_driver_coverage — C1 / C2 (V-DC-*)
# ===========================================================================

class TestDriverCoverageC1:

    def test_undriven_top_wire_output_c1_error(self):  # V-DC-01
        top = Module('t', interfaces=[_wire('o', 'out')])
        errs = [m for m in _validate(top)
                if m.category == 'connection' and m.level == 'error']
        assert len(errs) == 1
        assert "Top-level output 'o' has no driver" in errs[0].message
        assert errs[0].port == 'o'

    def test_instance_output_drives_net_suppresses_c1(self):  # V-DC-02
        child = _child('c', [_wire('oo', 'out')])
        top = Module('t',
                     interfaces=[_wire('topout', 'out')],
                     instances=[
                         _inst('u', 'c', child, [Connection('oo', 'topout')]),
                     ])
        assert not any(m.category == 'connection' and m.level == 'error'
                       for m in _validate(top))

    def test_glue_output_drives_net_suppresses_c1(self):  # V-DC-03 glue
        top = Module('t',
                     interfaces=[_wire('o', 'out')],
                     glue_logic=[GlueLogicEntry('g', 'passthrough', 'o',
                                                input='some_in')])
        assert not any(m.category == 'connection' and m.level == 'error'
                       for m in _validate(top))

    def test_assign_target_drives_net_suppresses_c1(self):  # V-DC-03 assign
        top = Module('t',
                     interfaces=[_wire('o', 'out')],
                     assigns=[Assign(target='o', expr='1\'b1')])
        assert not any(m.category == 'connection' and m.level == 'error'
                       for m in _validate(top))

    def test_instance_input_port_does_not_drive(self):  # V-DC-04
        # child i is an INPUT; connecting i->o does NOT drive o, C1 still fires.
        child = _child('c', [_wire('i', 'in')])
        top = Module('t',
                     interfaces=[_wire('o', 'out')],
                     instances=[
                         _inst('u', 'c', child, [Connection('i', 'o')]),
                     ])
        errs = [m for m in _validate(top)
                if m.category == 'connection' and m.level == 'error']
        assert len(errs) == 1
        assert "Top-level output 'o' has no driver" in errs[0].message

    def test_unconnected_conn_skipped(self):  # V-DC-05
        # child oo (out) -> 'unconnected' must NOT mark anything driven, and the
        # separate top output o stays undriven -> C1 on o still fires.
        child = _child('c', [_wire('oo', 'out')])
        top = Module('t',
                     interfaces=[_wire('o', 'out')],
                     instances=[
                         _inst('u', 'c', child, [Connection('oo', 'unconnected')]),
                     ])
        errs = [m for m in _validate(top)
                if m.category == 'connection' and m.level == 'error']
        assert len(errs) == 1
        assert errs[0].port == 'o'

    def test_hierarchical_conn_not_flat_net(self):  # V-DC-06
        # conn oo -> other.foo is hierarchical: _flat_net returns None, so it
        # neither drives nor references a flat net. The top output o therefore
        # stays undriven (C1) and unreferenced isn't relevant for an output.
        # (The dangling 'other.foo' ref ALSO now fires the V-XR unknown-
        # instance error — filter to the C1 message under test here.)
        child = _child('c', [_wire('oo', 'out')])
        top = Module('t',
                     interfaces=[_wire('o', 'out')],
                     instances=[
                         _inst('u', 'c', child, [Connection('oo', 'other.foo')]),
                     ])
        errs = [m for m in _validate(top)
                if m.category == 'connection' and m.level == 'error'
                and m.port == 'o']
        assert len(errs) == 1
        assert 'no driver' in errs[0].message

    def test_non_wire_top_output_not_c1_scoped(self):  # V-DC-07
        # ahb initiator top iface is is_output True but type != 'wire' -> the
        # type guard excludes it from C1.
        top = Module('t', interfaces=[
            Interface('m', type='ahb', direction='initiator')])
        assert not any(m.category == 'connection' for m in _validate(top))

    def test_child_none_unresolved_inst_references_not_drives(self):  # V-DC-08
        # Unresolved instance (child None) -> child_ports {} -> conn references
        # the net but cannot drive it. Top output o stays undriven -> C1 fires.
        top = Module('t',
                     interfaces=[_wire('o', 'out')],
                     instances=[
                         Instance(instance_name='u', module_name='nope',
                                  resolved_module=None,
                                  connections=[Connection('x', 'o')]),
                     ])
        errs = [m for m in _validate(top)
                if m.category == 'connection' and m.level == 'error']
        assert len(errs) == 1
        assert errs[0].port == 'o'


class TestDriverCoverageC2:

    def test_declared_top_wire_never_referenced_c2_warning(self):  # V-DC-09
        top = Module('t', interfaces=[_wire('din', 'in')])
        warns = [m for m in _validate(top)
                 if m.category == 'connection' and m.level == 'warning']
        assert len(warns) == 1
        assert "declared but never referenced" in warns[0].message
        assert warns[0].port == 'din'

    def test_c1_flagged_port_not_double_reported_by_c2(self):  # V-DC-10
        # An undriven OUTPUT is both undriven (C1) and unreferenced; C2 must
        # skip it -> exactly 1 message (the C1 error).
        top = Module('t', interfaces=[_wire('o', 'out')])
        msgs = [m for m in _validate(top) if m.category == 'connection']
        assert len(msgs) == 1
        assert msgs[0].level == 'error'

    def test_interconnect_conn_references_net_suppresses_c2(self):  # V-DC-11
        top = Module('t',
                     interfaces=[_wire('din', 'in')],
                     interconnects=[Interconnect('ic', connections=[
                         Connection('hclk', 'din')])])
        assert not any(m.category == 'connection' and m.level == 'warning'
                       for m in _validate(top))

    def test_glue_input_references_net_suppresses_c2(self):  # V-DC-12 input
        top = Module('t',
                     interfaces=[_wire('din', 'in')],
                     glue_logic=[GlueLogicEntry('g', 'passthrough', 'someout',
                                                input='din')])
        assert not any(m.category == 'connection' and m.level == 'warning'
                       and m.port == 'din' for m in _validate(top))

    def test_glue_concat_inputs_references_net_suppresses_c2(self):  # V-DC-12 concat
        top = Module('t',
                     interfaces=[_wire('din', 'in')],
                     glue_logic=[GlueLogicEntry('g', 'concat', 'someout',
                                                concat_inputs=[{'bit': 0,
                                                                'signal': 'din'}])])
        assert not any(m.category == 'connection' and m.level == 'warning'
                       and m.port == 'din' for m in _validate(top))

    def test_glue_inputs_list_references_net_suppresses_c2(self):  # V-DC-12 inputs
        top = Module('t',
                     interfaces=[_wire('din', 'in')],
                     glue_logic=[GlueLogicEntry('g', 'or_combine', 'someout',
                                                inputs=['din', 'other'])])
        assert not any(m.category == 'connection' and m.level == 'warning'
                       and m.port == 'din' for m in _validate(top))

    def test_instance_conn_any_direction_references_net_suppresses_c2(self):  # V-DC-13
        # child i is an INPUT; conn i->din references din even though it does
        # not drive it -> C2 suppressed.
        child = _child('c', [_wire('i', 'in')])
        top = Module('t',
                     interfaces=[_wire('din', 'in')],
                     instances=[
                         _inst('u', 'c', child, [Connection('i', 'din')]),
                     ])
        assert not any(m.category == 'connection' and m.level == 'warning'
                       for m in _validate(top))

    def test_non_wire_top_port_skipped_by_c2(self):  # V-DC-14
        # An ahb target top iface is unreferenced but non-wire -> no C2.
        top = Module('t', interfaces=[
            Interface('m', type='ahb', direction='target')])
        assert not any(m.category == 'connection' for m in _validate(top))


class TestDriverCoverageMix:

    def test_c1_and_c2_coexist(self):  # V-DC-15
        # Feeds V-PR-01 / V-PR-02: 1 C1 error + 1 C2 warning.
        top = Module('t', interfaces=[
            _wire('o', 'out'),   # undriven output -> C1 error
            _wire('din', 'in'),  # unreferenced input -> C2 warning
        ])
        msgs = [m for m in _validate(top) if m.category == 'connection']
        errs = [m for m in msgs if m.level == 'error']
        warns = [m for m in msgs if m.level == 'warning']
        assert len(errs) == 1 and errs[0].port == 'o'
        assert len(warns) == 1 and warns[0].port == 'din'


# ===========================================================================
# Cross-instance reference resolution (V-XR-*) — REGRESSION for the
# silently-severed-net class (nanosoc_m0_soc pynq_z2_build.md §8.5): a
# `conn: <inst>.<port>` that resolves to NOTHING on the referenced instance
# used to generate RTL with a dangling flat wire (undriven USRT PREADY hung
# the APB bus on the first UART write). These must now be loud errors.
# ===========================================================================

def _apb_child(name='debug_ss'):
    """A debug-ss-like module whose APB bundle is named DEBUG_APB but whose
    RTL response ports are DEBUG_P* — i.e. NOT resolvable members."""
    return _child(name, [
        Interface('DEBUG_APB', type='apb', direction='target',
                  params={'ADDR_WIDTH': 10, 'DATA_WIDTH': 32}),
    ])


def _axis_child(name='hostio'):
    return _child(name, [
        Interface('USRT0_RXD', type='axis_byte', direction='out'),
    ])


class TestXrefReferenceResolution:

    def test_unresolvable_member_reference_errors(self):  # V-XR-01
        """The exact USRT hang shape: DEBUG_PRDATA is neither an interface
        nor a member of DEBUG_APB (base-name mismatch), and the referenced
        instance never connects it -> ERROR, not a silent dangling wire."""
        top = Module('t', instances=[
            _inst('u_sysctrl', child=_child('sysctrl', [
                _wire('USRT_PRDATA', 'in', 32)]),
                connections=[Connection(port='USRT_PRDATA',
                                        conn='u_debug.DEBUG_PRDATA')]),
            _inst('u_debug', module_name='debug_ss', child=_apb_child()),
        ])
        errs = [m for m in _validate(top)
                if m.level == 'error' and m.category == 'connection']
        assert len(errs) == 1
        assert 'DEBUG_PRDATA' in errs[0].message
        assert 'u_debug' in errs[0].message

    def test_canonical_member_reference_ok(self):  # V-XR-02
        top = Module('t', instances=[
            _inst('u_sysctrl', child=_child('sysctrl', [
                _wire('RXD_TVALID', 'in', 1)]),
                connections=[Connection(port='RXD_TVALID',
                                        conn='u_hostio.USRT0_RXD_TVALID')]),
            _inst('u_hostio', module_name='hostio', child=_axis_child()),
        ])
        assert not [m for m in _validate(top) if m.level == 'error']

    def test_reference_to_explicitly_connected_port_ok(self):  # V-XR-03
        """A port that is not declared in the module yaml but that the
        referenced instance CONNECTS explicitly resolves through that
        connection in the generator — no error."""
        top = Module('t',
                     internal_wires=[Interface('net', type='wire',
                                               direction='internal',
                                               params={'WIDTH': 1})],
                     instances=[
            _inst('u_a', child=_child('a', [_wire('din', 'in', 1)]),
                  connections=[Connection(port='din',
                                          conn='u_b.UNDECLARED_OUT')]),
            _inst('u_b', module_name='b', child=_child('b'),
                  connections=[Connection(port='UNDECLARED_OUT',
                                          conn='net')]),
        ])
        assert not [m for m in _validate(top)
                    if m.level == 'error' and m.category == 'connection']

    def test_bundle_to_bundle_reference_errors(self):  # V-XR-04
        """Referencing a whole protocol bundle (u_hostio.USRT0_RXD) is not a
        bindable RTL port — must be a loud error telling the user to bind
        per member (previously emitted a bogus 1-bit `.IFACE(wire)`)."""
        top = Module('t', instances=[
            _inst('u_sysctrl', child=_child('sysctrl', [
                Interface('USRT0_RXD', type='axis_byte', direction='in')]),
                connections=[Connection(port='USRT0_RXD',
                                        conn='u_hostio.USRT0_RXD')]),
            _inst('u_hostio', module_name='hostio', child=_axis_child()),
        ])
        errs = [m for m in _validate(top)
                if m.level == 'error' and m.category == 'connection']
        assert len(errs) == 1
        assert 'bundle' in errs[0].message.lower()

    def test_unknown_instance_reference_errors(self):  # V-XR-05
        top = Module('t', instances=[
            _inst('u_a', child=_child('a', [_wire('din', 'in', 1)]),
                  connections=[Connection(port='din', conn='u_ghost.PORT')]),
        ])
        errs = [m for m in _validate(top) if m.level == 'error']
        assert any('u_ghost' in m.message for m in errs)

    def test_interconnect_reference_skipped(self):  # V-XR-06
        top = Module('t',
                     interconnects=[Interconnect(name='soc_ic')],
                     instances=[
            _inst('u_a', child=_child('a', [
                Interface('m', type='ahb', direction='initiator')]),
                connections=[Connection(port='m', conn='soc_ic.cpu_0')]),
        ])
        assert not [m for m in _validate(top)
                    if m.level == 'error' and m.category == 'connection']

    def test_unresolved_referenced_module_skipped(self):  # V-XR-07
        """resolved_module=None already warns via _validate_connections; the
        xref check must not pile an error on top."""
        top = Module('t', instances=[
            _inst('u_a', child=_child('a', [_wire('din', 'in', 1)]),
                  connections=[Connection(port='din', conn='u_b.SOMEPORT')]),
            _inst('u_b', module_name='missing', child=None),
        ])
        assert not [m for m in _validate(top)
                    if m.level == 'error' and m.category == 'connection']

    def test_glue_input_unresolvable_reference_errors(self):  # V-XR-08
        top = Module('t',
                     glue_logic=[GlueLogicEntry(
                         name='g', type='passthrough', output='o',
                         input='u_a.GHOST_PORT')],
                     instances=[_inst('u_a', child=_child('a'))])
        errs = [m for m in _validate(top) if m.level == 'error']
        assert any('GHOST_PORT' in m.message for m in errs)

    def test_decorated_member_reference_ok(self):  # V-XR-09
        """socdebug-style `_o`-decorated member spelling resolves."""
        top = Module('t', instances=[
            _inst('u_hostio', child=_child('hostio', [
                Interface('ADP_RXD', type='axis_byte', direction='in')]),
                connections=[Connection(port='ADP_RXD_TVALID',
                                        conn='u_debug.ADP_RXD_TVALID_o')]),
            _inst('u_debug', module_name='debug_ss', child=_child(
                'debug_ss', [Interface('ADP_RXD', type='axis_byte',
                                       direction='out')])),
        ])
        assert not [m for m in _validate(top)
                    if m.level == 'error' and m.category == 'connection']
