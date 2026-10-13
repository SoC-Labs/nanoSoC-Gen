# Copyright 2026, SoC Labs (www.soclabs.org)
"""Unit tests for ``soc_model/builder.py`` + the ``soc_model/model.py`` dataclasses.

Tier: ``unit`` (pure-Python, no EDA tools). Most tests call the private
``_build_*`` helpers directly with literal dicts so the YAML/parser layer is not
involved; the full ``build_system`` / ``_resolve_instances`` paths are driven via
the hermetic fixture SoCs (``minimal_soc`` / ``two_region_soc``) and small
``tmp_path`` libraries.

Covers TEST_PLAN section 5.3:
  * build_system ValueError on no ``module:`` key / missing top file
  * _build_module field mapping + the last-writer-wins cache short-circuit
  * _build_interface / _build_instance / _build_interconnect mapping + defaults
  * _resolve_instances: resolved_module attach, shared-instance cache, the
    unresolved->None path, rtl_module stub, recursion, $PARAM passthrough
  * model.py dataclass properties (Interface.width/addr_width/data_width/
    is_input/is_output/is_bidirectional, Connection.is_unconnected,
    Interconnect/InterconnectTarget/InterconnectInitiator defaults,
    Module helpers + flat_params, ResolvedSram, Instance/SramEntry/GlueLogicEntry)
"""

import pytest

from soc_model.parser import SoCParser
from soc_model.builder import SoCBuilder
from soc_model.model import (
    Connection, GlueLogicEntry, Instance, Interconnect, InterconnectInitiator,
    InterconnectInitiatorTarget, InterconnectTarget, Interface, Module, Param,
    ResolvedSram, SramEntry,
)

from tests.helpers import build_module_from_yaml

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Local builders (no filesystem) — exercise the pure _build_* mapping logic.
# ---------------------------------------------------------------------------
def _builder():
    """A SoCBuilder whose parser scans nothing external (cwd-only)."""
    return SoCBuilder(SoCParser(".", ["."]))


def _build_module(data):
    return _builder()._build_module(data)


# ===========================================================================
# build_system / _build_module
# ===========================================================================
class TestBuildSystem:
    def test_no_module_key_raises_valueerror(self, tmp_path):
        # B-01: a real top YAML file that simply has no `module:` key. The
        # parser returns the (non-empty) dict, build_system then raises.
        top = tmp_path / "nomod.yaml"
        top.write_text("not_a_module:\n  foo: bar\n")
        builder = SoCBuilder(SoCParser(str(tmp_path), [str(tmp_path)]))
        with pytest.raises(ValueError) as exc:
            builder.build_system("nomod.yaml")
        assert "No 'module:' key" in str(exc.value)
        assert "nomod.yaml" in str(exc.value)

    def test_missing_top_file_raises_valueerror(self, tmp_path, capsys):
        # B-02: missing file -> parser returns {} (+ warning) -> ValueError.
        builder = SoCBuilder(SoCParser(str(tmp_path), [str(tmp_path)]))
        with pytest.raises(ValueError) as exc:
            builder.build_system("nope.yaml")
        assert "No 'module:' key" in str(exc.value)
        out = capsys.readouterr().out
        assert "File not found" in out or "not found" in out.lower()

    def test_build_system_top_fields_and_source_file(self, fixtures_dir):
        # B-03: name/gen/desc populated; source_file is the top filename.
        top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
        assert top.name == "minimal_soc"
        assert top.gen is True
        assert top.desc != ""
        assert top.source_file == "minimal_soc.yaml"

    def test_build_system_resolves_top_instance(self, fixtures_dir):
        # B-04: _resolve_instances ran -> first instance has a resolved_module.
        top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
        assert top.instances, "fixture must declare an instance"
        assert top.instances[0].resolved_module is not None
        assert top.instances[0].resolved_module.name == "tiny_region"

    def test_module_get_defaults(self):
        # B-05: default name 'unknown'; gen False / desc '' / source_file ''.
        m = _build_module({"gen": True})
        assert m.name == "unknown"
        assert m.gen is True

        m2 = _build_module({"name": "x"})
        assert m2.name == "x"
        assert m2.gen is False
        assert m2.desc == ""
        assert m2.source_file == ""

    def test_cache_short_circuit_ignores_new_fields(self):
        # B-06: FOOT-GUN — same name returns first cached object; the second
        # dict's extra fields are silently ignored (last-writer-wins by NAME,
        # but first-writer-wins for the object itself).
        builder = _builder()
        a1 = builder._build_module({"name": "X"})
        a2 = builder._build_module({"name": "X", "desc": "second"})
        assert a1 is a2
        assert a2.desc == ""  # the 'second' desc never took effect

    def test_params_dict_form(self):
        # B-07: dict-form param -> Param(type, default, desc).
        m = _build_module({"name": "m", "params": {
            "P": {"type": "int", "default": 8, "desc": "width"}}})
        p = m.params["P"]
        assert isinstance(p, Param)
        assert p.type == "int"
        assert p.default == 8
        assert p.desc == "width"

    def test_params_scalar_form(self):
        # B-08: scalar param -> Param(default=val, type='int').
        m = _build_module({"name": "m", "params": {"PLAIN": 42}})
        p = m.params["PLAIN"]
        assert p.default == 42
        assert p.type == "int"

    def test_params_dict_missing_keys_defaults(self):
        # B-09: dict form missing keys -> .get defaults.
        m = _build_module({"name": "m", "params": {"P": {}}})
        p = m.params["P"]
        assert p.type == "int"
        assert p.default is None
        assert p.desc == ""

    def test_clocks_resets_defaults(self):
        # B-10: clocks/resets .get defaults; reset.active defaults 'low'.
        m = _build_module({
            "name": "m",
            "clocks": [{}],
            "resets": [{"name": "r"}],
        })
        assert m.clocks[0].name == ""
        assert m.clocks[0].source == ""
        assert m.resets[0].name == "r"
        assert m.resets[0].active == "low"

    def test_interfaces_built(self):
        # B-11: interfaces routed through _build_interface and appended.
        m = _build_module({"name": "m", "interfaces": [{"name": "clk"}]})
        assert len(m.interfaces) == 1
        iface = m.interfaces[0]
        assert iface.name == "clk"
        assert iface.type == "wire"
        assert iface.direction == "in"

    def test_assigns_str_coerced_and_defaults(self):
        # B-12: assign target/expr coerced to str; missing -> ''.
        m = _build_module({"name": "m", "assigns": [
            {"target": 1, "expr": 2, "type": "interrupt"}, {}]})
        assert m.assigns[0].target == "1"
        assert m.assigns[0].expr == "2"
        assert m.assigns[0].type == "interrupt"
        assert m.assigns[1].target == ""
        assert m.assigns[1].expr == ""
        assert m.assigns[1].type is None

    def test_internal_wires_direction_internal(self):
        # B-13: internal_wires -> Interface(direction='internal', type 'wire').
        m = _build_module({"name": "m", "internal_wires": [{"name": "w"}]})
        w = m.internal_wires[0]
        assert w.name == "w"
        assert w.direction == "internal"
        assert w.type == "wire"

    def test_glue_non_concat_str_coerced(self):
        # B-14: non-concat glue -> inputs+input coerced to str, concat_inputs [].
        m = _build_module({"name": "m", "glue_logic": [
            {"name": "g", "type": "or_combine", "output": "o",
             "inputs": [1, "a"], "input": 9}]})
        g = m.glue_logic[0]
        assert g.inputs == ["1", "a"]
        assert g.input == "9"
        assert g.concat_inputs == []

    def test_glue_concat_branch(self):
        # B-15: concat keeps structured concat_inputs, clears inputs, sets pad.
        m = _build_module({"name": "m", "glue_logic": [
            {"name": "g", "type": "concat", "output": "o",
             "inputs": [{"bit": 0, "signal": "s"}], "pad_zero_above": 4}]})
        g = m.glue_logic[0]
        assert g.concat_inputs == [{"bit": 0, "signal": "s"}]
        assert g.inputs == []
        assert g.pad_zero_above == 4

    def test_glue_input_none_stays_none(self):
        # B-16: when no `input:` key, glue.input stays None (not str-coerced).
        m = _build_module({"name": "m", "glue_logic": [
            {"name": "g", "type": "constant", "output": "o", "value": 0}]})
        assert m.glue_logic[0].input is None

    def test_srams_raw_values_no_resolution(self):
        # B-17: SRAM entries store RAW values (no $PARAM resolution); defaults.
        m = _build_module({"name": "m", "srams": [
            {"name": "r", "addr_width": "$N", "data_width": 32, "addr_unit": "word"}, {"name": "r2"}]})
        assert m.srams[0].addr_width == "$N"   # preserved verbatim
        assert m.srams[0].data_width == 32
        assert m.srams[1].addr_width == 14     # default
        assert m.srams[1].data_width == 32     # default

    def test_address_decode_firmware_build_info_presence(self):
        # B-18: present -> built object; absent -> None.
        with_all = _build_module({
            "name": "m",
            "address_decode": {"type": "ahb_slave_mux"},
            "firmware": {"cpu_initiator": "cpu_m"},
            "build_info": {"apb_slot": 3},
        })
        assert with_all.address_decode is not None
        assert with_all.firmware is not None
        assert with_all.build_info is not None

        without = _build_module({"name": "m2"})
        assert without.address_decode is None
        assert without.firmware is None
        assert without.build_info is None

    def test_minimal_module_optional_lists_empty(self):
        # B-19: a bare module has all optional lists empty.
        m = _build_module({"name": "x"})
        assert m.clocks == []
        assert m.resets == []
        assert m.interfaces == []
        assert m.instances == []
        assert m.assigns == []
        assert m.glue_logic == []
        assert m.internal_wires == []
        assert m.interconnects == []
        assert m.srams == []
        assert m.params == {}


# ===========================================================================
# _build_interface / _build_instance / _build_interconnect
# ===========================================================================
class TestBuildSubObjects:
    def test_interface_get_defaults(self):
        # B-20: full mapping + the empty-dict default set.
        b = _builder()
        full = b._build_interface(
            {"name": "i", "type": "ahb", "direction": "target",
             "params": {"A": 1}, "desc": "d"})
        assert full.name == "i"
        assert full.type == "ahb"
        assert full.direction == "target"
        assert full.params == {"A": 1}
        assert full.desc == "d"

        empty = b._build_interface({})
        assert empty.name == ""
        assert empty.type == "wire"
        assert empty.direction == "in"
        assert empty.params == {}

    def test_instance_module_form(self):
        # B-21: `module:` form -> is_rtl_module False.
        b = _builder()
        inst = b._build_instance({"instance_name": "u", "module": "child"})
        assert inst.instance_name == "u"
        assert inst.module_name == "child"
        assert inst.is_rtl_module is False

    def test_instance_rtl_module_form(self):
        # B-22: `rtl_module:` form -> is_rtl_module True.
        b = _builder()
        inst = b._build_instance({"instance_name": "u", "rtl_module": "rtlx"})
        assert inst.module_name == "rtlx"
        assert inst.is_rtl_module is True

    def test_instance_neither_module_nor_rtl(self):
        # B-23: neither key -> module_name '' and is_rtl_module False.
        b = _builder()
        inst = b._build_instance({"instance_name": "u"})
        assert inst.module_name == ""
        assert inst.is_rtl_module is False

    def test_instance_optional_defaults(self):
        # B-24: addressable/condition/params .get defaults.
        b = _builder()
        bare = b._build_instance({"instance_name": "u"})
        assert bare.addressable is False
        assert bare.condition is None
        assert bare.params == {}

        full = b._build_instance({
            "instance_name": "u", "module": "c",
            "addressable": True, "condition": "USE_X", "params": {"W": 8}})
        assert full.addressable is True
        assert full.condition == "USE_X"
        assert full.params == {"W": 8}

    def test_instance_connections_str_coerced(self):
        # B-25: connection port/conn coerced to str; desc default ''.
        b = _builder()
        inst = b._build_instance({"instance_name": "u", "module": "c",
                                  "connections": [{"port": 1, "conn": 2}, {}]})
        assert inst.connections[0].port == "1"
        assert inst.connections[0].conn == "2"
        assert inst.connections[0].desc == ""
        assert inst.connections[1].port == ""
        assert inst.connections[1].conn == ""

    def test_instance_inline_interfaces(self):
        # B-26: inline interfaces parsed into inline_interfaces (rtl_module).
        b = _builder()
        inst = b._build_instance({
            "instance_name": "u", "rtl_module": "r",
            "interfaces": [{"name": "clk"}]})
        assert len(inst.inline_interfaces) == 1
        assert inst.inline_interfaces[0].name == "clk"

    def test_interconnect_defaults(self):
        # B-27: gen default True, type 'ahb_lite', params {}.
        b = _builder()
        ic = b._build_interconnect({"name": "ic"})
        assert ic.name == "ic"
        assert ic.gen is True
        assert ic.type == "ahb_lite"
        assert ic.params == {}
        assert ic.targets == []
        assert ic.initiators == []

    def test_interconnect_target_dict_protocol_lowercased(self):
        # B-28: dict target -> fields; protocol .lower()'d.
        b = _builder()
        ic = b._build_interconnect({"name": "ic", "targets": [
            {"name": "t", "protocol": "APB", "base": 16, "size": 256}]})
        t = ic.targets[0]
        assert t.name == "t"
        assert t.protocol == "apb"
        assert t.base == 16
        assert t.size == 256

    def test_interconnect_target_bare_string(self):
        # B-29: bare string target -> name=str(t), defaults for the rest.
        b = _builder()
        ic = b._build_interconnect({"name": "ic", "targets": ["foo"]})
        t = ic.targets[0]
        assert t.name == "foo"
        assert t.base == 0
        assert t.size == 0
        assert t.protocol == "ahb"
        assert t.instance is None

    def test_interconnect_initiator_targets_dict_vs_str(self):
        # B-30: initiator targets dict carries visibility; str -> None.
        b = _builder()
        ic = b._build_interconnect({"name": "ic", "initiators": [
            {"name": "m", "targets": [
                {"name": "t", "visibility": [{"base": 0}]}, "t2"]}]})
        init = ic.initiators[0]
        assert init.targets[0].name == "t"
        assert init.targets[0].visibility == [{"base": 0}]
        assert init.targets[1].name == "t2"
        assert init.targets[1].visibility is None

    def test_interconnect_initiator_defaults(self):
        # B-31: initiator instance/passthrough default None; targets [].
        b = _builder()
        ic = b._build_interconnect({"name": "ic", "initiators": [{"name": "m"}]})
        init = ic.initiators[0]
        assert init.instance is None
        assert init.passthrough is None
        assert init.targets == []


# ===========================================================================
# _resolve_instances
# ===========================================================================
class TestResolveInstances:
    def test_normal_module_ref_resolved_with_source_file(self, fixtures_dir):
        # B-32: resolved_module attached, child source_file = modules/<name>.yaml.
        top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
        child = top.instances[0].resolved_module
        assert child is not None
        assert child.name == "tiny_region"
        assert child.source_file == "modules/tiny_region.yaml"

    def test_shared_module_same_object(self, fixtures_dir):
        # B-33: two instances of the same module share ONE Module object.
        top = build_module_from_yaml(fixtures_dir, "two_region_soc.yaml")
        i0, i1 = top.instances[0], top.instances[1]
        assert i0.resolved_module is not None
        assert i0.resolved_module is i1.resolved_module

    def test_missing_module_stays_none(self):
        # B-34: unresolved (parse_module -> None) leaves resolved_module None,
        # no exception.
        b = _builder()
        inst = Instance(instance_name="u", module_name="nosuch",
                        is_rtl_module=False)
        top = Module(name="top")
        top.instances.append(inst)
        b._resolve_instances(top)
        assert inst.resolved_module is None

    def test_discovered_dict_lacking_module_key_stays_none(self):
        # B-35: parse_module returns a dict WITHOUT a 'module' key -> the
        # `and 'module' in mod_data` guard keeps resolved_module None.
        b = _builder()
        b.parser.parse_module = lambda name: {"not_module": 1}
        inst = Instance(instance_name="u", module_name="x", is_rtl_module=False)
        top = Module(name="top")
        top.instances.append(inst)
        b._resolve_instances(top)
        assert inst.resolved_module is None

    def test_rtl_module_stub(self):
        # B-36: rtl_module -> stub Module(gen=False, desc='Standalone RTL...'),
        # interfaces copied from the inline interfaces.
        b = _builder()
        inst = Instance(instance_name="u", module_name="myrtl",
                        is_rtl_module=True)
        inst.inline_interfaces.append(Interface(name="clk"))
        inst.inline_interfaces.append(Interface(name="rst"))
        top = Module(name="top")
        top.instances.append(inst)
        b._resolve_instances(top)
        stub = inst.resolved_module
        assert stub is not None
        assert stub.name == "myrtl"
        assert stub.gen is False
        assert stub.desc == "Standalone RTL module: myrtl"
        assert [i.name for i in stub.interfaces] == ["clk", "rst"]

    def test_rtl_module_empty_stub(self):
        # B-37: rtl_module with no inline interfaces -> stub.interfaces == [].
        b = _builder()
        inst = Instance(instance_name="u", module_name="bare",
                        is_rtl_module=True)
        top = Module(name="top")
        top.instances.append(inst)
        b._resolve_instances(top)
        assert inst.resolved_module is not None
        assert inst.resolved_module.interfaces == []

    def test_recursion_into_grandchild(self, tmp_path):
        # B-38: resolution recurses — a grandchild instance is also resolved.
        (tmp_path / "grand.yaml").write_text(
            "module:\n  name: grand\n  interfaces:\n"
            "    - {name: g, type: wire, direction: in}\n")
        (tmp_path / "child.yaml").write_text(
            "module:\n  name: child\n  instances:\n"
            "    - {instance_name: u_g, module: grand}\n")
        (tmp_path / "top.yaml").write_text(
            "module:\n  name: top\n  instances:\n"
            "    - {instance_name: u_c, module: child}\n")
        top = build_module_from_yaml(tmp_path, "top.yaml")
        child = top.instances[0].resolved_module
        assert child is not None
        assert child.instances[0].resolved_module is not None
        assert child.instances[0].resolved_module.name == "grand"

    def test_param_passthrough_verbatim(self, fixtures_dir):
        # B-39: $PARAM refs on a child instance are NOT resolved by the builder
        # — they pass through verbatim into Instance.params.
        top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
        inst = top.instances[0]
        assert inst.params["SYS_ADDR_W"] == "$SYS_ADDR_W"
        assert inst.params["SYS_DATA_W"] == "$SYS_DATA_W"
        assert inst.params["RAM_ADDR_W"] == "$SRAM_RAM_ADDR_W"

    def test_rtl_stub_interfaces_alias_inline(self):
        # B-40: stub interfaces are the SAME Interface objects (aliasing).
        b = _builder()
        inst = Instance(instance_name="u", module_name="r", is_rtl_module=True)
        iface = Interface(name="clk")
        inst.inline_interfaces.append(iface)
        top = Module(name="top")
        top.instances.append(inst)
        b._resolve_instances(top)
        assert inst.resolved_module.interfaces[0] is iface


# ===========================================================================
# model.py dataclasses & properties
# ===========================================================================
class TestInterfaceProperties:
    def test_width_wire(self):
        # M-01: wire with no WIDTH -> 1; int WIDTH -> that int.
        assert Interface("w").width == 1
        assert Interface("w", params={"WIDTH": 8}).width == 8

    def test_width_str_is_none(self):
        # M-02: FOOT-GUN — a string WIDTH (unresolved $PARAM) -> None.
        assert Interface("w", params={"WIDTH": "8"}).width is None

    def test_width_non_wire_none(self):
        # M-03: non-wire type -> width always None.
        assert Interface("a", "ahb", params={"WIDTH": 8}).width is None

    def test_addr_width_primary_and_fallback(self):
        # M-04: ADDR_WIDTH int; ADDR_W fallback.
        assert Interface("a", "ahb", params={"ADDR_WIDTH": 12}).addr_width == 12
        assert Interface("a", "ahb", params={"ADDR_W": 10}).addr_width == 10

    def test_addr_width_zero_is_none(self):
        # M-05: FOOT-GUN — value 0 is falsy so `or` skips it -> None.
        assert Interface("a", "ahb", params={"ADDR_WIDTH": 0}).addr_width is None

    def test_addr_width_missing_or_str_none(self):
        # M-06: missing or str -> None.
        assert Interface("a", "ahb").addr_width is None
        assert Interface("a", "ahb", params={"ADDR_WIDTH": "12"}).addr_width is None

    def test_data_width_primary_and_axis_byte_default(self):
        # M-07: DATA_WIDTH int; axis_byte with no DATA_WIDTH -> 8.
        assert Interface("s", "ahb", params={"DATA_WIDTH": 32}).data_width == 32
        assert Interface("s", "axis_byte").data_width == 8

    def test_data_width_axis_byte_explicit_and_zero(self):
        # M-08: axis_byte with explicit DATA_WIDTH -> that int.
        assert Interface("s", "axis_byte", params={"DATA_WIDTH": 16}).data_width == 16
        # FOOT-GUN: DATA_WIDTH=0 is falsy, so `get('DATA_WIDTH') or get('DATA_W')`
        # collapses to None and the axis_byte default-8 branch fires (NOT None as
        # the design doc predicted). Pin the real code behaviour.
        assert Interface("s", "axis_byte", params={"DATA_WIDTH": 0}).data_width == 8
        # A non-axis_byte bus type with DATA_WIDTH=0 has no default branch -> None.
        assert Interface("s", "ahb", params={"DATA_WIDTH": 0}).data_width is None

    @pytest.mark.parametrize("direction,expected", [
        ("in", True), ("target", True), ("receiver", True),
        ("out", False), ("initiator", False), ("inout", False),
    ])
    def test_is_input(self, direction, expected):
        # M-09.
        assert Interface("x", direction=direction).is_input is expected

    @pytest.mark.parametrize("direction,expected", [
        ("out", True), ("initiator", True), ("sender", True),
        ("in", False), ("receiver", False), ("target", False),
    ])
    def test_is_output(self, direction, expected):
        # M-10: ASYMMETRY — receiver/target are inputs but NOT outputs.
        assert Interface("x", direction=direction).is_output is expected

    @pytest.mark.parametrize("direction,expected", [
        ("inout", True), ("in", False), ("out", False), ("target", False),
    ])
    def test_is_bidirectional(self, direction, expected):
        # M-11.
        assert Interface("x", direction=direction).is_bidirectional is expected


class TestConnection:
    def test_is_unconnected(self):
        # M-12.
        assert Connection("p", "unconnected").is_unconnected is True
        assert Connection("p", "some_net").is_unconnected is False

    def test_both_args_required(self):
        # M-13: conn is a required positional field.
        with pytest.raises(TypeError):
            Connection("p")  # noqa: missing conn


class TestInterconnectDataclasses:
    def test_interconnect_target_defaults(self):
        # M-14.
        t = InterconnectTarget("a")
        assert t.base == 0
        assert t.size == 0
        assert t.sw_access == "rw"
        assert t.protocol == "ahb"
        assert t.subordinate_bus is False
        assert t.instance is None
        assert t.phys_size is None
        assert t.region_type is None
        assert t.role is None
        assert t.apb_config is None
        assert t.passthrough is None

    def test_initiator_target_names_and_empty_default(self):
        # M-15.
        init = InterconnectInitiator("m", targets=[
            InterconnectInitiatorTarget("a"),
            InterconnectInitiatorTarget("b")])
        assert init.target_names == ["a", "b"]
        assert InterconnectInitiator("m").targets == []
        assert InterconnectInitiator("m").target_names == []

    def test_interconnect_defaults(self):
        # M-16.
        ic = Interconnect("ic")
        assert ic.gen is True
        assert ic.type == "ahb_lite"
        assert ic.desc == ""
        assert ic.params == {}
        assert ic.connections == []
        assert ic.targets == []
        assert ic.initiators == []


class TestModuleDataclass:
    def test_module_defaults(self):
        # M-17.
        m = Module("t")
        assert m.gen is False
        assert m.desc == ""
        assert m.source_file == ""
        assert m.params == {}
        assert m.clocks == []
        assert m.resets == []
        assert m.interfaces == []
        assert m.instances == []
        assert m.assigns == []
        assert m.glue_logic == []
        assert m.internal_wires == []
        assert m.interconnects == []
        assert m.srams == []
        assert m.address_decode is None
        assert m.firmware is None
        assert m.build_info is None

    def test_get_interface(self):
        # M-18: match / missing / empty-list.
        m = Module("t")
        assert m.get_interface("x") is None      # empty list
        a = Interface("a")
        m.interfaces.extend([a, Interface("b")])
        assert m.get_interface("a") is a
        assert m.get_interface("b") is not None
        assert m.get_interface("z") is None      # populated but no match

    def test_get_instance_and_interconnect(self):
        # M-19.
        m = Module("t")
        inst = Instance("u")
        ic = Interconnect("ic")
        m.instances.append(inst)
        m.interconnects.append(ic)
        assert m.get_instance("u") is inst
        assert m.get_instance("nope") is None
        assert m.get_interconnect("ic") is ic
        assert m.get_interconnect("nope") is None

    def test_flat_params(self):
        # M-20: {name: default}; empty -> {}.
        m = Module("t")
        assert m.flat_params == {}
        m.params["W"] = Param("W", default=8)
        m.params["D"] = Param("D", default=None)
        assert m.flat_params == {"W": 8, "D": None}


class TestMiscDataclasses:
    def test_resolved_sram_depth_size(self):
        # M-21.
        s = ResolvedSram("r", addr_width=10, data_width=32)
        assert s.depth == 1024
        assert s.size_bytes == 4096

    def test_instance_defaults(self):
        # M-22.
        i = Instance("u")
        assert i.module_name == ""
        assert i.is_rtl_module is False
        assert i.addressable is False
        assert i.condition is None
        assert i.params == {}
        assert i.connections == []
        assert i.inline_interfaces == []
        assert i.resolved_module is None

    def test_sram_and_glue_defaults(self):
        # M-23.
        s = SramEntry("r")
        assert s.addr_width == 14
        assert s.data_width == 32

        g = GlueLogicEntry("g", "passthrough", "o")
        assert g.inputs == []
        assert g.concat_inputs == []
        assert g.input is None
        assert g.value is None
        assert g.width is None
        assert g.pad_zero_above is None
