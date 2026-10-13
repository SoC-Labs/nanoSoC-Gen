# Copyright 2026, SoC Labs (www.soclabs.org)
"""Smoke + light-structural tests for the 'misc' backends.

Covers, per the backends-misc area (TEST_PLAN 5.10 + 5.12):

  * python.py       SoCPythonBackend  — importable .py model dump + _h/_var_name
  * sram.py         SoCSramBackend     — SRAM location report + _fmt_size/_walk
  * build_info.py   SoCBuildInfoBackend — build-info register map (.yaml/.h) + helpers
  * system.py       SoCSystemBackend    — system-level SV wrapper + _build_* context
  * chip.py         SoCChipBackend      — chip + pad wrappers
  * lint.py         FlistParser / SoCLintBackend — flist parse + negative lint paths
  * html.py         SoCVisualizer       — smoke only (no golden: 110 KB f-string)

These are deterministic and tool-light:
  * No EDA tool, no perl, no ARM_IP_LIBRARY_PATH.
  * jinja2 IS available in this env (system/chip render); the Environment-None
    branch of each jinja2 backend is exercised by monkeypatching the symbol to
    None so the no-jinja2 fallback path is still covered without uninstalling.
  * pyslang is NOT installed here, so the real-lint paths are tooling/SKIP; the
    FlistParser and the CLI-not-found negative path are pure-Python unit-testable.

No goldens are committed: html is smoke-only by policy, and system/chip inject
``datetime.now()`` into the rendered template body in places the snapshot helper
does not strip — structural substring asserts give the test semantic teeth
without a fragile/non-deterministic committed artifact.
"""

import importlib
import json
import subprocess
from pathlib import Path

import pytest

from soc_model.model import (
    BuildInfo, BuildInfoField, Instance, Interface, Module, Param, SramEntry,
    Interconnect, InterconnectInitiator, InterconnectInitiatorTarget,
    InterconnectTarget, AddressDecode, AddressDecodeSlot, RegisterMap,
    Register, RegisterField,
)
from tests.helpers import build_module_from_yaml

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Shared inline-module factories (no fixture for *_system exists, so build one).
# ---------------------------------------------------------------------------

def _system_module(name="foo_system"):
    """A minimal '*_system' Module that the system/chip jinja templates render.

    Carries one wire, one gpio, one swd iface (driving the three port-expansion
    branches in SoCSystemBackend._build_ports) plus an int param >= 2**31 (the
    32'hXXXXXXXX formatting branch) and one internal wire with a $PARAM width.
    """
    m = Module(name=name, gen=True, desc="system wrapper fixture")
    m.params = {
        "GPIO_W": Param(name="GPIO_W", type="int", default=16),
        # >= 2**31 -> formatted as 32'hXXXXXXXX by _build_params
        "BIG": Param(name="BIG", type="int", default=0x80000000),
    }
    m.interfaces = [
        Interface(name="sys_hclk", type="wire", direction="in",
                  params={"WIDTH": 1}, desc="AHB clock"),
        Interface(name="gpio0", type="gpio", direction="inout",
                  params={"WIDTH": 16}, desc="GPIO bus"),
        Interface(name="cpu_0_swd", type="swd", direction="in",
                  params={}, desc="SWD debug"),
    ]
    # one internal wire with a $PARAM width + one that collides with a port name
    m.internal_wires = [
        Interface(name="dbg_net", type="wire", direction="internal",
                  params={"WIDTH": "$GPIO_W"}, desc="internal"),
        Interface(name="sys_hclk", type="wire", direction="internal",
                  params={"WIDTH": 1}, desc="collides with port -> skipped"),
    ]
    # one child instance (region_exp -> is_expansion True) with a $-param + an
    # unconnected port (-> signal None branch)
    inst = Instance(instance_name="u_region_exp_0", module_name="region_exp_0")
    inst.params = {"WIDTH": "$GPIO_W"}
    from soc_model.model import Connection
    inst.connections = [
        Connection(port="HCLK", conn="sys_hclk"),
        Connection(port="SPARE", conn="unconnected"),
    ]
    m.instances = [inst]
    return m


# ===========================================================================
# python.py  — SoCPythonBackend  (PY-01..06)
# ===========================================================================

class TestPythonBackend:
    def test_generate_writes_importable_py(self, fixtures_dir, tmp_path):
        """SoCPythonBackend.generate writes a non-empty, syntactically-valid .py
        that binds top_module and references the model constructors."""
        from soc_model.backends.python import SoCPythonBackend
        top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
        out = tmp_path / "minimal_soc_model.py"
        SoCPythonBackend(top).generate(str(out))

        src = out.read_text()
        assert src.strip(), "python backend wrote empty output"
        # structural teeth: imports the model + binds the top variable
        assert "from soc_model.model import (" in src
        assert "top_module = " in src
        assert f"name={top.name!r}" in src
        # PY-01 round-trip: the emitted source must compile.
        compile(src, str(out), "exec")

    def test_roundtrip_reconstructs_module(self, fixtures_dir, tmp_path):
        """Executing the emitted .py reconstructs a Module with the same name
        and instance count (the round-trip contract PY-01)."""
        from soc_model.backends.python import SoCPythonBackend
        top = build_module_from_yaml(fixtures_dir, "two_region_soc.yaml")
        out = tmp_path / "two_region_model.py"
        SoCPythonBackend(top).generate(str(out))

        ns = {}
        exec(compile(out.read_text(), str(out), "exec"), ns)
        rebuilt = ns["top_module"]
        assert rebuilt.name == top.name
        assert rebuilt.gen == top.gen
        assert len(rebuilt.instances) == len(top.instances)
        assert isinstance(rebuilt, Module)

    def test_h_hex_threshold(self):
        """PY-03: _h renders ints >= 0x100 as 0x%08X, smaller values as repr."""
        from soc_model.backends.python import _h
        assert _h(0x10000000) == "0x10000000"
        assert _h(0x100) == "0x00000100"
        assert _h(4) == "4"          # below threshold -> repr
        assert _h(0xFF) == "255"

    def test_var_name_sanitises(self):
        """PY-04: _var_name prefixes mod_ and replaces '-'/'.' with '_'."""
        from soc_model.backends.python import _var_name
        assert _var_name("a-b.c") == "mod_a_b_c"
        assert _var_name("plain") == "mod_plain"

    def test_repr_assign_bit_zero_emitted(self):
        """PY-05: _repr_assign emits bit=0 (guarded by 'is not None', not truthiness)
        and omits optional kwargs when falsy."""
        from soc_model.backends.python import SoCPythonBackend
        from soc_model.model import Assign
        be = SoCPythonBackend(Module(name="t"))
        s = be._repr_assign(Assign(target="o", expr="i", bit=0))
        assert "bit=0" in s
        assert s.startswith("Assign(")
        # combining is falsy by default -> not emitted
        assert "combining=" not in s

    # --- PY-07: every optional kwarg of _repr_assign present ---------------
    def test_repr_assign_all_optionals_emitted(self):
        """PY-07: _repr_assign emits type / bit / combining / origin when each is
        set (covers python.py L152,154,156,158) and the emitted call round-trips
        through the model constructor."""
        from soc_model.backends.python import SoCPythonBackend
        from soc_model.model import Assign
        be = SoCPythonBackend(Module(name="t"))
        s = be._repr_assign(Assign(
            target="o", expr="i", type="wire", bit=3,
            combining="or", origin="glue", desc="d"))
        assert "type='wire'" in s
        assert "bit=3" in s
        assert "combining='or'" in s
        assert "origin='glue'" in s
        # the emitted fragment must reconstruct an equivalent Assign.
        ns = {"Assign": Assign}
        rebuilt = eval(s, ns)  # noqa: S307 — fixed generator output, test-only
        assert rebuilt.target == "o" and rebuilt.bit == 3
        assert rebuilt.combining == "or" and rebuilt.origin == "glue"

    # --- PY-08: generate() over a populated SoC drives _repr_module branches,
    #            _repr_interconnect / target / initiator, and the dedup return.
    def test_generate_two_region_drives_interconnect_and_module_branches(
            self, fixtures_dir, tmp_path):
        """PY-08: re-point generate() at two_region_soc (params + interfaces +
        instances-with-connections/params/resolved_module + a gen:True
        interconnect with targets and an initiator) so _repr_interconnect (and
        its InterconnectTarget / InterconnectInitiator / InterconnectInitiatorTarget
        emission), the params/interfaces/instances branches of _repr_module, and
        the duplicate-module early-return in _collect_modules all execute.

        The emitted .py must compile AND reconstruct the interconnect with its
        two targets, their bases, and the initiator's visibility list intact."""
        from soc_model.backends.python import SoCPythonBackend
        top = build_module_from_yaml(fixtures_dir, "two_region_soc.yaml")
        # precondition: the fixture really exercises the interconnect path.
        assert len(top.interconnects) == 1
        ic_src = top.interconnects[0]
        assert ic_src.gen is True and len(ic_src.targets) == 2
        assert len(ic_src.initiators) == 1

        out = tmp_path / "two_region_model.py"
        SoCPythonBackend(top).generate(str(out))
        src = out.read_text()
        compile(src, str(out), "exec")

        # structural teeth on the interconnect emission.
        assert "interconnects=[" in src
        assert "Interconnect(" in src
        assert "InterconnectTarget(" in src
        assert "InterconnectInitiator(" in src
        assert "InterconnectInitiatorTarget(" in src
        # _h emits base 0 as repr ('0', < 0x100 threshold) and the high base in
        # the 0x%08X form (>= 0x100).
        assert "base=0," in src
        assert "base=0x10000000" in src
        # tiny_region (shared by u_sram_0 + u_sram_1) is emitted exactly once —
        # the second instance hits the _collect_modules dedup early-return.
        assert src.count("mod_tiny_region = Module(") == 1

        # full round-trip: rebuild and assert the interconnect survived.
        ns = {}
        exec(compile(src, str(out), "exec"), ns)
        rebuilt = ns["top_module"]
        assert rebuilt.name == "two_region_soc"
        assert len(rebuilt.interconnects) == 1
        ic = rebuilt.interconnects[0]
        assert ic.gen is True
        bases = sorted(t.base for t in ic.targets)
        assert bases == [0x00000000, 0x10000000]
        assert len(ic.initiators) == 1
        vis = {t.name for t in ic.initiators[0].targets}
        assert vis == {"sram_0", "sram_1"}
        # params + interfaces branches of _repr_module preserved.
        assert set(rebuilt.params) >= {"SYS_ADDR_W", "SYS_DATA_W"}
        assert {i.name for i in rebuilt.interfaces} >= {"sys_hclk", "cpu_m"}

    # --- PY-09: generate() over the firmware fixture drives _repr_firmware. --
    def test_generate_firmware_drives_firmware_repr(self, fixtures_dir, tmp_path):
        """PY-09: re-point generate() at firmware_soc (a firmware: block with two
        linker_profiles, one of which carries a size_adjust region, plus an adp
        dict) so _repr_firmware (L276-296) and its LinkerProfile / LinkerRegion
        emission run. The .py compiles and reconstructs the firmware tree."""
        from soc_model.backends.python import SoCPythonBackend
        top = build_module_from_yaml(fixtures_dir, "firmware_soc.yaml")
        assert top.firmware is not None
        assert top.firmware.cpu_initiator == "cpu_m"
        # the fixture has no hex_adjust; set one so the optional hex_adjust
        # branch (L297-298) is also exercised.
        top.firmware.hex_adjust = "0x100"

        out = tmp_path / "fw_model.py"
        SoCPythonBackend(top).generate(str(out))
        src = out.read_text()
        compile(src, str(out), "exec")

        assert "firmware=Firmware(" in src
        assert "cpu_initiator='cpu_m'" in src
        assert "linker_profiles=[" in src
        assert "LinkerProfile(" in src
        assert "LinkerRegion(" in src
        # the dmem region carries size_adjust=-0x400 -> -1024, exercising the
        # optional-region-kwarg branch (L285).
        assert "size_adjust=-1024" in src
        # the adp dict is emitted (L295-296).
        assert "adp=" in src
        # hex_adjust emitted (L297-298).
        assert "hex_adjust='0x100'" in src

        ns = {}
        exec(compile(src, str(out), "exec"), ns)
        rebuilt = ns["top_module"]
        assert rebuilt.firmware is not None
        assert rebuilt.firmware.cpu_initiator == "cpu_m"
        names = {lp.name for lp in rebuilt.firmware.linker_profiles}
        assert names == {"app_imem", "stage0_bootrom"}
        assert rebuilt.firmware.adp == {
            "upload_target": "imem_0", "address_select": "default"}
        # the size_adjust survived the round-trip on the right region.
        adjusts = [
            r.size_adjust
            for lp in rebuilt.firmware.linker_profiles
            for r in lp.regions
            if r.size_adjust is not None
        ]
        assert -1024 in adjusts

    # --- PY-10: an address_decode with a register-map slot drives the
    #            address-decode / slot / register-map / register reprs. -------
    def test_generate_address_decode_register_map_reprs(self, tmp_path):
        """PY-10: build a Module carrying an AddressDecode whose slot has a
        resolved_register_map with two registers (one full: reset_value + desc +
        fields; one bare: none of those), so _repr_address_decode (L223-230),
        _repr_slot (L232-245), _repr_register_map (L247-257) and _repr_register
        (both the with-optionals and the no-optionals path, L259-274) all run.

        The emitted .py compiles and reconstructs the full register map."""
        from soc_model.backends.python import SoCPythonBackend
        from soc_model.model import (
            AddressDecode, AddressDecodeSlot, RegisterMap, Register, RegisterField,
        )
        rm = RegisterMap(
            name="tiny_regs", module="tiny_apb_periph", desc="map",
            address_width=12, data_width=32,
            registers=[
                # full register: reset_value + desc + fields (L264,266,268-273)
                Register(
                    name="CTRL", offset=0x000, width=32, access="RW",
                    reset_value=0, desc="control",
                    fields=[
                        RegisterField(name="ENABLE", bits="0", access="RW",
                                      desc="enable bit"),
                        RegisterField(name="MODE", bits="2:1", access="RW",
                                      desc="mode select"),
                    ],
                ),
                # bare register: no reset_value / desc / fields -> none of the
                # optional branches taken (the L274 minimal return).
                Register(name="STATUS", offset=0x004, width=32, access="RO"),
            ],
        )
        # a nested address_decode hanging off the slot drives the recursive
        # _repr_address_decode call in _repr_slot (L243-244).
        nested = AddressDecode(type="apb", module="sub_decoder", slots=[
            AddressDecodeSlot(slot=0, name="sub", module="sub_periph",
                              offset=0x200, size=0x100),
        ])
        slot = AddressDecodeSlot(
            slot=0, name="periph", module="tiny_apb_periph",
            offset=0x1000, size=0x1000,
            register_map="tiny_regs", resolved_register_map=rm,
            desc="register slot", address_decode=nested,
        )
        ad = AddressDecode(
            type="apb", module="apb_decoder", bridge="ahb_to_apb", slots=[slot])
        m = Module(name="ad_soc", gen=True, desc="address-decode SoC")
        m.address_decode = ad

        out = tmp_path / "ad_model.py"
        SoCPythonBackend(m).generate(str(out))
        src = out.read_text()
        compile(src, str(out), "exec")

        # structural teeth across all four reprs.
        assert "address_decode=AddressDecode(" in src
        assert "bridge='ahb_to_apb'" in src
        assert "AddressDecodeSlot(" in src
        assert "resolved_register_map=RegisterMap(" in src
        assert "Register(" in src
        assert "RegisterField(" in src
        # the slot offset (>= 0x100) is emitted via _h in 0x%08X form.
        assert "offset=0x00001000" in src
        assert "reset_value=0" in src       # CTRL reset_value branch (L265)

        ns = {}
        exec(compile(src, str(out), "exec"), ns)
        rebuilt = ns["top_module"]
        rrm = rebuilt.address_decode.slots[0].resolved_register_map
        assert rrm.name == "tiny_regs"
        assert rrm.address_width == 12 and rrm.data_width == 32
        regs = {r.name: r for r in rrm.registers}
        assert set(regs) == {"CTRL", "STATUS"}
        assert regs["CTRL"].reset_value == 0
        assert [f.name for f in regs["CTRL"].fields] == ["ENABLE", "MODE"]
        # bare STATUS: no fields emitted, defaults preserved.
        assert regs["STATUS"].fields == []
        assert regs["STATUS"].access == "RO"
        # the nested slot address_decode round-tripped (L243-244).
        nested_rt = rebuilt.address_decode.slots[0].address_decode
        assert nested_rt is not None
        assert nested_rt.module == "sub_decoder"
        assert nested_rt.slots[0].name == "sub"

    # --- PY-11: instance condition + inline_interfaces + module assigns. ----
    def test_generate_instance_condition_inline_iface_and_assigns(self, tmp_path):
        """PY-11: a Module whose single instance has a condition and an
        inline_interface, plus a top-level assign, drives the instance
        condition branch (L170), the inline_interfaces branch (L177-178) and the
        assigns branch of _repr_module (L124-125). Round-trips."""
        from soc_model.backends.python import SoCPythonBackend
        from soc_model.model import Assign, Connection, Instance, Interface
        inst = Instance(
            instance_name="u_x", module_name="x", is_rtl_module=True)
        inst.condition = "$EN"
        inst.params = {"W": "$WID"}
        inst.connections = [Connection(port="C", conn="c")]
        inst.inline_interfaces = [
            Interface(name="ahb_s", type="ahb", direction="target",
                      params={"ADDR_WIDTH": 32}, desc="inline target"),
        ]
        m = Module(name="inst_soc", gen=True)
        m.instances = [inst]
        m.assigns = [
            Assign(target="top_o", expr="inner", type="wire",
                   combining="and", origin="gen", desc="glue"),
        ]

        out = tmp_path / "inst_model.py"
        SoCPythonBackend(m).generate(str(out))
        src = out.read_text()
        compile(src, str(out), "exec")

        assert "condition='$EN'" in src           # L170
        assert "inline_interfaces=[" in src       # L177-178
        assert "assigns=[" in src                 # L124-125
        assert "combining='and'" in src

        ns = {}
        exec(compile(src, str(out), "exec"), ns)
        rebuilt = ns["top_module"]
        ri = rebuilt.instances[0]
        assert ri.condition == "$EN"
        assert [i.name for i in ri.inline_interfaces] == ["ahb_s"]
        assert [a.target for a in rebuilt.assigns] == ["top_o"]
        assert rebuilt.assigns[0].combining == "and"


# ===========================================================================
# sram.py  — SoCSramBackend  (SR-01..05)
# ===========================================================================

class TestSramBackend:
    def test_fmt_size_buckets(self):
        """SR: sram._fmt_size MB/KB/B buckets. NOTE sram.py has NO GB bucket
        (unlike text.py) so 1 GiB renders as '1024 MB' — pin current behaviour."""
        from soc_model.backends.sram import _fmt_size
        assert _fmt_size(1024 * 1024) == "1 MB"
        assert _fmt_size(2 * 1024) == "2 KB"
        assert _fmt_size(16) == "16 B"
        assert _fmt_size(1536) == "1.5 KB"          # non-integer KB
        assert _fmt_size(0x40000000) == "1024 MB"   # no GB tier in sram.py

    def test_no_srams_block(self, fixtures_dir, tmp_path):
        """SR-01: a design with no SRAM entries renders the 'No SRAMs' message,
        not a table."""
        from soc_model.backends.sram import SoCSramBackend
        # minimal_soc's tiny_region uses an sram inside the gen:False region —
        # but a bare Module with no instances/srams definitely has none.
        top = Module(name="empty_soc", gen=True)
        out = tmp_path / "sram.txt"
        SoCSramBackend(top).generate(str(out))
        txt = out.read_text()
        assert "No SRAMs declared" in txt
        assert "SRAM Location Report" in txt
        assert "Depth" not in txt  # table header absent

    def test_walk_resolves_param_widths(self, tmp_path):
        """SR-02: _walk resolves $PARAM addr/data widths against effective params;
        an unresolved/non-int falls back to defaults 14/32."""
        from soc_model.backends.sram import SoCSramBackend
        top = Module(name="ram_soc", gen=True)
        top.params = {"AW": Param(name="AW", type="int", default=10)}
        top.srams = [
            SramEntry(name="ok_ram", addr_width="$AW", data_width=32, desc="resolved", addr_unit="word"),
            SramEntry(name="bad_ram", addr_width="$UNRESOLVED", data_width=32, addr_unit="word",
                      desc="falls back"),
        ]
        out = tmp_path / "sram.txt"
        SoCSramBackend(top).generate(str(out))
        txt = out.read_text()
        assert "ok_ram" in txt and "bad_ram" in txt
        assert "Total: 2 SRAM(s)" in txt
        # ok_ram: depth 2**10 = 1024; bad_ram falls back to 2**14 = 16384
        assert "1024" in txt
        assert "16384" in txt
        # hierarchy path is dotted from the top name
        assert "ram_soc.ok_ram" in txt

    def test_walk_recurses_child_overrides(self, tmp_path):
        """SR-03: child effective params = child defaults overlaid by instance
        overrides resolved against the parent's effective params."""
        from soc_model.backends.sram import SoCSramBackend
        child = Module(name="child", gen=False)
        child.params = {"AW": Param(name="AW", type="int", default=8)}
        child.srams = [SramEntry(name="c_ram", addr_width="$AW", data_width=32, addr_unit="word")]
        parent = Module(name="parent", gen=True)
        parent.params = {"PAW": Param(name="PAW", type="int", default=12)}
        inst = Instance(instance_name="u_child", module_name="child")
        inst.params = {"AW": "$PAW"}      # override child's AW with parent's PAW=12
        inst.resolved_module = child
        parent.instances = [inst]

        out = tmp_path / "sram.txt"
        SoCSramBackend(parent).generate(str(out))
        txt = out.read_text()
        # AW overridden 8 -> 12 -> depth 2**12 = 4096, dotted path parent.u_child.c_ram
        assert "4096" in txt
        assert "parent.u_child.c_ram" in txt


# ===========================================================================
# build_info.py  — SoCBuildInfoBackend  (BI-01..12)
# ===========================================================================

class TestBuildInfoBackend:
    def test_no_build_info_returns_none(self, tmp_path):
        """BI-01: a Module without a build_info section -> None, no files."""
        from soc_model.backends.build_info import SoCBuildInfoBackend
        top = Module(name="nobi", gen=True)
        assert SoCBuildInfoBackend(top).generate(tmp_path) is None
        assert not list(tmp_path.iterdir())

    def test_header_and_field_registers(self, tmp_path):
        """BI-02/03/04/09: header magic/version/count + int32 + string fields,
        and the RegisterMap name/module/width contract."""
        from soc_model.backends.build_info import (
            SoCBuildInfoBackend, _BUILD_INFO_MAGIC, _BUILD_INFO_VERSION,
        )
        bi = BuildInfo(gen=True, fields=[
            BuildInfoField(name="BUILD_ID", type="int32", value=7, desc="id"),
            BuildInfoField(name="BAD_INT", type="int32", value="x", desc="non-int"),
            BuildInfoField(name="SOC", type="string", value="ABCDEF",
                           max_length=6, desc="name"),
        ])
        top = Module(name="mysoc", gen=True, build_info=bi)
        res = SoCBuildInfoBackend(top).generate(tmp_path, project_dir=str(tmp_path))
        assert res is not None
        rm, yaml_path = res

        # BI-09 RegisterMap contract
        assert rm.name == "mysoc_build_info"
        assert rm.module == "mysoc_build_info"
        assert rm.gen is True
        assert rm.address_width == 12

        by_name = {r.name: r for r in rm.registers}
        # BI-02 header
        assert by_name["BUILD_INFO_ID"].reset_value == _BUILD_INFO_MAGIC == 0x42494E46
        assert by_name["BUILD_INFO_VERSION"].reset_value == _BUILD_INFO_VERSION == 1
        assert by_name["BUILD_INFO_FIELD_COUNT"].reset_value == len(bi.fields) == 3
        # BI-03 int32 (non-int -> 0)
        assert by_name["BUILD_ID"].reset_value == 7
        assert by_name["BAD_INT"].reset_value == 0
        # BI-04 string -> ceil(6/4)*4 = 8 bytes -> 2 regs named _0/_1
        assert "SOC_0" in by_name and "SOC_1" in by_name
        # all registers read-only, contiguous 4-byte offsets
        assert all(r.access == "RO" for r in rm.registers)
        offsets = [r.offset for r in rm.registers]
        assert offsets == list(range(0, 4 * len(rm.registers), 4))

    def test_encode_string_registers_le_pack(self):
        """BI-05: _encode_string_registers packs 4 ASCII bytes/reg little-endian,
        truncating + null-padding to max_length."""
        from soc_model.backends.build_info import _encode_string_registers
        # 'ABCD' = 0x41,42,43,44 LE -> 0x44434241
        assert _encode_string_registers("ABCD", 4) == [0x44434241]
        # over-length truncated to max_length
        assert _encode_string_registers("ABCDEFGH", 4) == [0x44434241]
        # short string null-padded
        assert _encode_string_registers("AB", 4) == [0x00004241]

    def test_string_field_max_len_round_up(self, tmp_path):
        """BI-04: max_length is rounded UP to a multiple of 4 (5 -> 8 -> 2 regs)."""
        from soc_model.backends.build_info import SoCBuildInfoBackend
        bi = BuildInfo(gen=True, fields=[
            BuildInfoField(name="S", type="string", value="HELLO", max_length=5),
        ])
        top = Module(name="t", gen=True, build_info=bi)
        rm, _ = SoCBuildInfoBackend(top).generate(tmp_path, project_dir=str(tmp_path))
        string_regs = [r for r in rm.registers if r.name.startswith("S_")]
        assert [r.name for r in string_regs] == ["S_0", "S_1"]

    def test_timestamp_field_shape_only(self, tmp_path):
        """BI-06: a timestamp field captures int(time.time()) — assert SHAPE
        (positive int), never golden the value."""
        from soc_model.backends.build_info import SoCBuildInfoBackend
        bi = BuildInfo(gen=True, fields=[
            BuildInfoField(name="TS", type="timestamp"),
        ])
        top = Module(name="t", gen=True, build_info=bi)
        rm, _ = SoCBuildInfoBackend(top).generate(tmp_path, project_dir=str(tmp_path))
        ts = next(r for r in rm.registers if r.name == "TS")
        assert isinstance(ts.reset_value, int) and ts.reset_value > 0

    def test_git_hash_field_shape_only(self, tmp_path):
        """BI-07: a git_hash field produces an int reg (value depends on the
        repo; assert shape only)."""
        from soc_model.backends.build_info import SoCBuildInfoBackend
        bi = BuildInfo(gen=True, fields=[
            BuildInfoField(name="GH", type="git_hash"),
        ])
        top = Module(name="t", gen=True, build_info=bi)
        rm, _ = SoCBuildInfoBackend(top).generate(tmp_path, project_dir=str(tmp_path))
        gh = next(r for r in rm.registers if r.name == "GH")
        assert isinstance(gh.reset_value, int)
        assert gh.reset_value >= 0

    def test_capture_git_hash_non_git_dir(self, tmp_path):
        """BI-08: _capture_git_hash on a non-git directory returns 0."""
        from soc_model.backends.build_info import _capture_git_hash
        assert _capture_git_hash(str(tmp_path)) == 0

    def test_yaml_and_c_header_written(self, tmp_path):
        """BI-10/11/12: both artifacts land; the C header defines _OFFSET (and
        _VALUE when a reset is present), and the YAML carries the register map."""
        from soc_model.backends.build_info import SoCBuildInfoBackend
        bi = BuildInfo(gen=True, fields=[
            BuildInfoField(name="BUILD_ID", type="int32", value=0xABCD, desc="id"),
        ])
        top = Module(name="mysoc", gen=True, build_info=bi)
        rm, yaml_path = SoCBuildInfoBackend(top).generate(
            tmp_path, project_dir=str(tmp_path))

        # BI-11 YAML structural (timestamp '# Generated:' line is stripped by the
        # snapshot helper, but we assert structure directly, not via golden).
        ytxt = yaml_path.read_text()
        assert "register_map:" in ytxt
        assert "BUILD_INFO_ID" in ytxt
        assert "BUILD_ID" in ytxt

        # BI-10/12 C header: the /* Generated */ block comment is NOT
        # timestamp-stripped, so assert only the deterministic define lines.
        h_path = tmp_path / "firmware" / f"{rm.name}.h"
        assert h_path.exists()
        htxt = h_path.read_text()
        assert "#define MYSOC_BUILD_INFO_BUILD_INFO_ID_OFFSET  0x000U" in htxt
        assert "#define MYSOC_BUILD_INFO_BUILD_ID_OFFSET  0x00CU" in htxt
        assert "#define MYSOC_BUILD_INFO_BUILD_ID_VALUE   0x0000ABCDU" in htxt
        assert htxt.startswith("/* Auto-generated build info")

    def test_generate_on_firmware_fixture(self, fixtures_dir, tmp_path):
        """End-to-end on the real firmware_soc fixture (build_info: int32 + string)."""
        from soc_model.backends.build_info import SoCBuildInfoBackend
        top = build_module_from_yaml(fixtures_dir, "firmware_soc.yaml")
        res = SoCBuildInfoBackend(top).generate(tmp_path, project_dir=str(tmp_path))
        assert res is not None
        rm, yaml_path = res
        assert rm.name == "firmware_soc_build_info"
        names = {r.name for r in rm.registers}
        assert {"BUILD_INFO_ID", "BUILD_ID", "SOC_NAME_0"} <= names
        assert yaml_path.exists()


# ===========================================================================
# system.py  — SoCSystemBackend  (SY-01..06)
# ===========================================================================

class TestSystemBackend:
    def test_environment_none_returns_none(self, tmp_path, monkeypatch):
        """SY-01: jinja2 unavailable (Environment is None) -> warn + return None."""
        import soc_model.backends.system as system_mod
        monkeypatch.setattr(system_mod, "Environment", None)
        be = system_mod.SoCSystemBackend(_system_module())
        assert be.generate(tmp_path, tmp_path) is None

    def test_generate_renders_system_sv(self, tmp_path):
        """SY-06 (structural, not golden — datetime in body): renders a non-empty
        <name>.sv containing the module, expanded swd/gpio ports, and the flist."""
        from soc_model.backends.system import SoCSystemBackend
        sysm = _system_module("foo_system")
        out = SoCSystemBackend(sysm).generate(tmp_path, tmp_path)
        assert out is not None and out.exists()
        sv = out.read_text()
        assert sv.strip()
        assert "foo_system" in sv
        # swd expansion: cpu_0_swd -> cpu_0_swdi/swclk/swdo/swdoen
        assert "cpu_0_swdi" in sv
        assert "cpu_0_swclk" in sv
        # gpio expansion
        assert "gpio0_in" in sv and "gpio0_out" in sv and "gpio0_outen" in sv
        # flist written into the explicitly-passed flist_dir (== tmp_path here)
        assert (tmp_path / "foo_system_system.flist").exists()

    def test_build_params_big_int_hex(self):
        """SY-02: an int param >= 2**31 is formatted 32'hXXXXXXXX."""
        from soc_model.backends.system import SoCSystemBackend
        be = SoCSystemBackend(_system_module())
        params = {p["name"]: p["default"] for p in be._build_params()}
        assert params["BIG"] == "32'h80000000"
        assert params["GPIO_W"] == 16          # small int left as-is

    def test_build_ports_expansion(self):
        """SY-03: wire / swd (_swd strip) / gpio (default w16) expansion."""
        from soc_model.backends.system import SoCSystemBackend
        be = SoCSystemBackend(_system_module())
        ports = {p["name"]: p for p in be._build_ports()}
        # wire
        assert ports["sys_hclk"]["direction"] == "input"
        # swd base strips the _swd suffix
        assert "cpu_0_swdi" in ports and "cpu_0_swclk" in ports
        # gpio default width 16
        assert ports["gpio0_in"]["width"] == 16

    def test_build_internal_wires_skip_and_resolve(self):
        """SY-04: a wire whose name collides with a port is skipped; a $PARAM
        width is resolved against the flat params."""
        from soc_model.backends.system import SoCSystemBackend
        be = SoCSystemBackend(_system_module())
        wires = {w["name"]: w for w in be._build_internal_wires()}
        assert "sys_hclk" not in wires      # collides with port -> skipped
        assert wires["dbg_net"]["width"] == 16   # $GPIO_W resolved to 16

    def test_build_instances_expansion_flag_and_unconnected(self):
        """SY-05: is_expansion True when 'region_exp' in module_name; a $-prefixed
        param has its $ stripped; an unconnected port -> signal None."""
        from soc_model.backends.system import SoCSystemBackend
        be = SoCSystemBackend(_system_module())
        inst = be._build_instances()[0]
        assert inst["is_expansion"] is True
        pvals = {p["name"]: p["value"] for p in inst["params"]}
        assert pvals["WIDTH"] == "GPIO_W"      # $ stripped
        conns = {c["port"]: c["signal"] for c in inst["connections"]}
        assert conns["SPARE"] is None          # unconnected -> None
        assert conns["HCLK"] == "sys_hclk"


# ===========================================================================
# chip.py  — SoCChipBackend  (CH-01..04)
# ===========================================================================

class TestChipBackend:
    def test_environment_none_returns_none_pair(self, tmp_path, monkeypatch):
        """CH-01: jinja2 unavailable -> (None, None)."""
        import soc_model.backends.chip as chip_mod
        monkeypatch.setattr(chip_mod, "Environment", None)
        be = chip_mod.SoCChipBackend(_system_module())
        assert be.generate(tmp_path, tmp_path) == (None, None)

    def test_chip_name_derivation(self):
        """CH-02: '_system' -> '_chip'; core_name falls back when core is None."""
        from soc_model.backends.chip import SoCChipBackend
        be = SoCChipBackend(_system_module("foo_system"))
        ctx = be._build_context()
        assert ctx["module_name"] == "foo_chip"
        assert ctx["system_module_name"] == "foo_system"
        # core_name fallback: foo_chip -> foo
        assert ctx["core_module_name"] == "foo"

    def test_generate_writes_chip_and_pads(self, tmp_path):
        """CH-03/04 (structural, not golden): writes <chip>.v and <chip>_pads.v
        with GPIO ports resolved; the pads module name is <chip>_pads."""
        from soc_model.backends.chip import SoCChipBackend
        sysm = _system_module("foo_system")
        chip_path, pads_path = SoCChipBackend(sysm).generate(tmp_path, tmp_path)
        assert chip_path is not None and chip_path.exists()
        assert pads_path is not None and pads_path.exists()
        assert chip_path.name == "foo_chip.v"
        assert pads_path.name == "foo_chip_pads.v"
        chip_txt = chip_path.read_text()
        pads_txt = pads_path.read_text()
        assert chip_txt.strip() and pads_txt.strip()
        assert "foo_chip" in chip_txt
        assert "foo_chip_pads" in pads_txt
        # flist emitted
        assert (tmp_path / "foo_chip_chip.flist").exists()

    def test_gpio_ports_width_resolved(self):
        """CH-03: gpio_ports in the context are WIDTH-resolved to ints."""
        from soc_model.backends.chip import SoCChipBackend
        ctx = SoCChipBackend(_system_module())._build_context()
        gpio = {g["name"]: g for g in ctx["gpio_ports"]}
        assert gpio["gpio0"]["width"] == 16
        assert isinstance(gpio["gpio0"]["width"], int)


# ===========================================================================
# lint.py  — FlistParser + SoCLintBackend  (LN-01..13)
# ===========================================================================

class TestFlistParser:
    def test_expand_vars_known_and_unknown(self, tmp_path, monkeypatch):
        """LN-01: $(VAR) expands from env; unknown vars are left verbatim."""
        from soc_model.backends.lint import FlistParser
        monkeypatch.setenv("FOO", "/a")
        p = FlistParser()
        assert p._expand_vars("$(FOO)/x.sv") == "/a/x.sv"
        assert p._expand_vars("$(NOPE)/x.sv") == "$(NOPE)/x.sv"

    def test_env_override_dict(self, tmp_path):
        """The env override dict augments os.environ for $(VAR) expansion."""
        from soc_model.backends.lint import FlistParser
        p = FlistParser({"BAR": "/b"})
        assert p._expand_vars("$(BAR)/y.sv") == "/b/y.sv"

    def test_recursion_guard(self, tmp_path):
        """LN-02: re-parsing an already-visited flist is a no-op (no dup files)."""
        from soc_model.backends.lint import FlistParser
        (tmp_path / "top.sv").write_text("module top; endmodule")
        fl = tmp_path / "main.flist"
        fl.write_text("top.sv\n")
        p = FlistParser()
        p.parse(str(fl))
        p.parse(str(fl))
        assert len(p.files) == 1

    def test_missing_flist_warns_no_raise(self, tmp_path, capsys):
        """LN-03: a missing flist prints a WARNING and returns (no exception)."""
        from soc_model.backends.lint import FlistParser
        p = FlistParser()
        p.parse(str(tmp_path / "ghost.flist"))   # must not raise
        assert p.files == []
        assert "WARNING" in capsys.readouterr().out

    def test_skips_comments_and_blanks(self, tmp_path):
        """LN-04: blank lines and // comment lines are skipped."""
        from soc_model.backends.lint import FlistParser
        (tmp_path / "a.sv").write_text("module a; endmodule")
        fl = tmp_path / "c.flist"
        fl.write_text("// header comment\n\n   \na.sv\n")
        p = FlistParser()
        p.parse(str(fl))
        assert [Path(f).name for f in p.files] == ["a.sv"]

    def test_f_include_and_incdir(self, tmp_path):
        """LN-05/06/07: -f recursive include (rel->flist_dir), +incdir+ split on
        '+' (rel->flist_dir), and other +.../-y lines skipped."""
        from soc_model.backends.lint import FlistParser
        (tmp_path / "top.sv").write_text("module top; endmodule")
        (tmp_path / "inc1").mkdir()
        (tmp_path / "inc2").mkdir()
        sub = tmp_path / "sub.flist"
        sub.write_text("top.sv\n")
        main = tmp_path / "main.flist"
        main.write_text(
            "+incdir+inc1+inc2\n"
            "-f sub.flist\n"
            "+libext+.v\n"
            "-y libdir\n"
        )
        p = FlistParser()
        p.parse(str(main))
        assert [Path(f).name for f in p.files] == ["top.sv"]
        incnames = {Path(d).name for d in p.incdirs}
        assert {"inc1", "inc2"} <= incnames

    def test_missing_source_warns_and_dedup(self, tmp_path, capsys):
        """LN-08: a missing source path warns; existing files are de-duplicated."""
        from soc_model.backends.lint import FlistParser
        (tmp_path / "real.sv").write_text("module real; endmodule")
        fl = tmp_path / "m.flist"
        fl.write_text("real.sv\nreal.sv\nmissing.sv\n")
        p = FlistParser()
        p.parse(str(fl))
        assert [Path(f).name for f in p.files] == ["real.sv"]   # deduped
        assert "WARNING" in capsys.readouterr().out


class TestLintBackend:
    def test_slang_bin_resolution(self):
        """LN-09: explicit arg > which('slang') > 'slang' literal fallback."""
        from soc_model.backends.lint import SoCLintBackend
        assert SoCLintBackend(slang_bin="/x/custom_slang").slang_bin == "/x/custom_slang"
        # no arg -> some non-empty resolution (which('slang') or 'slang')
        assert SoCLintBackend().slang_bin

    def test_build_env_paths(self, tmp_path):
        """LN-10: build_env constructs all SOCLABS_* keys from the project tree."""
        from soc_model.backends.lint import SoCLintBackend
        env = SoCLintBackend.build_env(str(tmp_path))
        for key in (
            "SOCLABS_PROJECT_DIR", "SOCLABS_NANOSOC_SOC_DIR",
            "SOCLABS_NANOSOC_ARCH_TECH_DIR", "SOCLABS_SOCDEBUG_TECH_DIR",
            "SOCLABS_SLCOREM0_TECH_DIR", "SOCLABS_HOSTIO4_TECH_DIR",
        ):
            assert key in env
        assert env["SOCLABS_PROJECT_DIR"] == str(tmp_path)
        assert env["SOCLABS_NANOSOC_SOC_DIR"].endswith("nanosoc_m0_soc")

    def test_collect_rtl_files_sorted(self, tmp_path):
        """LN-11: collect_rtl_files rglobs *.sv + *.v, each set sorted."""
        from soc_model.backends.lint import SoCLintBackend
        (tmp_path / "b.sv").write_text("")
        (tmp_path / "a.sv").write_text("")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "c.v").write_text("")
        files = [f.name for f in SoCLintBackend().collect_rtl_files(tmp_path)]
        # *.sv collected (sorted) then *.v collected (sorted)
        assert files == ["a.sv", "b.sv", "c.v"]

    def test_lint_no_rtl_files(self, tmp_path):
        """LN-12: with no RTL files lint_and_report prints a message and PASSes."""
        from soc_model.backends.lint import SoCLintBackend
        assert SoCLintBackend().lint_and_report(tmp_path) is True

    def test_lint_cli_slang_absent(self, tmp_path):
        """LN-13: a bogus slang binary -> FileNotFoundError caught -> returncode
        -1 + an install-hint message (no raise)."""
        from soc_model.backends.lint import SoCLintBackend
        (tmp_path / "x.sv").write_text("module x; endmodule")
        be = SoCLintBackend(slang_bin=str(tmp_path / "no_such_slang"))
        rc, out, err = be._lint_cli([str(tmp_path / "x.sv")])
        assert rc == -1
        assert "slang" in err
        assert "pip install pyslang" in err

    def test_lint_and_report_returns_false_when_slang_missing(self, tmp_path):
        """LN-13 (integration): with real RTL but no pyslang and a bogus slang
        binary, lint_and_report returns False (and does not raise)."""
        from soc_model.backends.lint import SoCLintBackend, _HAS_PYSLANG
        if _HAS_PYSLANG:
            pytest.skip("pyslang installed: CLI fallback path not taken")
        (tmp_path / "x.sv").write_text("module x; endmodule")
        be = SoCLintBackend(slang_bin=str(tmp_path / "no_such_slang"))
        assert be.lint_and_report(tmp_path) is False


@pytest.mark.tooling
def test_lint_real_pyslang_or_skip(tmp_path):
    """LN-14: the real pyslang lint path only runs when pyslang is importable;
    otherwise SKIP cleanly (never fail)."""
    from soc_model.backends.lint import _HAS_PYSLANG, SoCLintBackend
    if not _HAS_PYSLANG:
        pytest.skip("pyslang not installed — real lint path unavailable")
    (tmp_path / "ok.sv").write_text("module ok; endmodule\n")
    # Clean RTL should lint without errors (returns True).
    assert SoCLintBackend().lint_and_report(tmp_path) is True


# ---------------------------------------------------------------------------
# Fake-pyslang scaffolding (LN-15..18): drives _lint_pyslang (151-166) and the
# pyslang branch of lint_and_report (218-228) WITHOUT the real binding, by
# injecting a faithful stand-in for the handful of pyslang symbols the backend
# touches: Compilation/SyntaxTree.fromFile/addSyntaxTree/getAllDiagnostics,
# trees[0].sourceManager, DiagnosticEngine.reportAll, and diag.isError().
# ---------------------------------------------------------------------------

class _FakeDiag:
    def __init__(self, is_error: bool):
        self._is_error = is_error

    def isError(self):
        return self._is_error


class _FakeTree:
    def __init__(self, path):
        self.path = path
        # the backend reads trees[0].sourceManager and hands it to reportAll
        self.sourceManager = f"sm:{path}"


class _FakeCompilation:
    """Records the trees added and yields a fixed diagnostic list."""
    def __init__(self, diags):
        self._diags = diags
        self.added = []

    def addSyntaxTree(self, tree):
        self.added.append(tree)

    def getAllDiagnostics(self):
        return self._diags


class _FakeSyntaxTree:
    last_paths = []

    @staticmethod
    def fromFile(path):
        _FakeSyntaxTree.last_paths.append(path)
        return _FakeTree(path)


class _FakeDiagnosticEngine:
    last_args = None

    @staticmethod
    def reportAll(sm, diags):
        _FakeDiagnosticEngine.last_args = (sm, diags)
        return f"REPORT[{len(diags)} diag(s) from {sm}]"


def _make_fake_pyslang(diags):
    """Build a module-like namespace exposing the pyslang API surface the
    backend uses, wired to return ``diags`` from getAllDiagnostics()."""
    import types
    fake = types.SimpleNamespace()
    fake.Compilation = lambda: _FakeCompilation(diags)
    fake.SyntaxTree = _FakeSyntaxTree
    fake.DiagnosticEngine = _FakeDiagnosticEngine
    return fake


class TestLintPyslangPath:
    """_lint_pyslang (151-166) + lint_and_report pyslang branch (218-228)."""

    def setup_method(self):
        _FakeSyntaxTree.last_paths = []
        _FakeDiagnosticEngine.last_args = None

    def test_pyslang_no_diagnostics(self, tmp_path, monkeypatch):
        """LN-15: no diagnostics -> (0, 0, '') and the tree set is built from
        every input file (covers 151-160 short-circuit)."""
        import soc_model.backends.lint as lint_mod
        monkeypatch.setattr(lint_mod, "pyslang", _make_fake_pyslang([]),
                            raising=False)
        be = lint_mod.SoCLintBackend()
        files = [str(tmp_path / "a.sv"), str(tmp_path / "b.sv")]
        errs, warns, report = be._lint_pyslang(files)
        assert (errs, warns, report) == (0, 0, "")
        # every file was turned into a SyntaxTree
        assert _FakeSyntaxTree.last_paths == files

    def test_pyslang_counts_errors_and_warnings(self, tmp_path, monkeypatch):
        """LN-16: mixed diagnostics -> error/warning split + reportAll text,
        driving the full 151-166 body (sourceManager + reportAll + counts)."""
        import soc_model.backends.lint as lint_mod
        diags = [_FakeDiag(True), _FakeDiag(False), _FakeDiag(True)]
        monkeypatch.setattr(lint_mod, "pyslang", _make_fake_pyslang(diags),
                            raising=False)
        be = lint_mod.SoCLintBackend()
        errs, warns, report = be._lint_pyslang([str(tmp_path / "x.sv")])
        assert errs == 2          # two isError() == True
        assert warns == 1         # len - errors
        assert "REPORT[3 diag(s)" in report
        # reportAll was called with the FIRST tree's sourceManager
        sm, passed_diags = _FakeDiagnosticEngine.last_args
        assert sm == f"sm:{tmp_path / 'x.sv'}"
        assert passed_diags is diags

    def test_lint_and_report_pyslang_pass(self, tmp_path, monkeypatch, capsys):
        """LN-17: lint_and_report takes the pyslang branch (HAS_PYSLANG True),
        prints the report + a PASS, and returns True with zero errors (218-225)."""
        import soc_model.backends.lint as lint_mod
        diags = [_FakeDiag(False), _FakeDiag(False)]   # warnings only
        monkeypatch.setattr(lint_mod, "_HAS_PYSLANG", True, raising=False)
        monkeypatch.setattr(lint_mod, "pyslang", _make_fake_pyslang(diags),
                            raising=False)
        (tmp_path / "ok.sv").write_text("module ok; endmodule")
        be = lint_mod.SoCLintBackend()
        assert be.lint_and_report(tmp_path) is True
        out = capsys.readouterr().out
        assert "Using pyslang" in out
        assert "0 error(s), 2 warning(s)" in out
        assert "Lint PASSED." in out
        assert "REPORT[2 diag(s)" in out   # report.strip() printed (220-221)

    def test_lint_and_report_pyslang_fail(self, tmp_path, monkeypatch, capsys):
        """LN-18: a pyslang error makes lint_and_report return False + print
        'Lint FAILED.' (226-228)."""
        import soc_model.backends.lint as lint_mod
        diags = [_FakeDiag(True)]
        monkeypatch.setattr(lint_mod, "_HAS_PYSLANG", True, raising=False)
        monkeypatch.setattr(lint_mod, "pyslang", _make_fake_pyslang(diags),
                            raising=False)
        (tmp_path / "bad.sv").write_text("module bad")  # not that it parses
        be = lint_mod.SoCLintBackend()
        assert be.lint_and_report(tmp_path) is False
        out = capsys.readouterr().out
        assert "1 error(s), 0 warning(s)" in out
        assert "Lint FAILED." in out


class TestCollectFromFlist:
    """collect_from_flist (143-146): build_env + FlistParser -> (files, incdirs)."""

    def test_collect_files_and_incdirs(self, tmp_path):
        """LN-19: collect_from_flist parses a flist (with -f include + +incdir+)
        and returns the de-duplicated source set and the include-dir list."""
        from soc_model.backends.lint import SoCLintBackend
        (tmp_path / "top.sv").write_text("module top; endmodule")
        (tmp_path / "leaf.sv").write_text("module leaf; endmodule")
        (tmp_path / "inc").mkdir()
        sub = tmp_path / "sub.flist"
        sub.write_text("leaf.sv\n")
        flist = tmp_path / "design.flist"
        flist.write_text(
            "+incdir+inc\n"
            "top.sv\n"
            "-f sub.flist\n"
        )
        files, incdirs = SoCLintBackend().collect_from_flist(
            str(flist), project_dir=str(tmp_path))
        assert [Path(f).name for f in files] == ["top.sv", "leaf.sv"]
        assert any(Path(d).name == "inc" for d in incdirs)

    def test_collect_uses_build_env_vars(self, tmp_path):
        """LN-20: collect_from_flist seeds FlistParser with build_env, so a
        $(SOCLABS_PROJECT_DIR) reference inside the flist resolves."""
        from soc_model.backends.lint import SoCLintBackend
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "by_var.sv").write_text("module by_var; endmodule")
        flist = tmp_path / "v.flist"
        # absolute path built from the env var injected by build_env
        flist.write_text("$(SOCLABS_PROJECT_DIR)/by_var.sv\n")
        files, _ = SoCLintBackend().collect_from_flist(
            str(flist), project_dir=str(proj))
        assert [Path(f).name for f in files] == ["by_var.sv"]


class TestLintCliFakeSubprocess:
    """_lint_cli (174-188) via a monkeypatched subprocess.run — covers the
    -I/extra_args command assembly, the success-return path, and the timeout
    branch, with no real linter present."""

    def test_cli_builds_command_and_returns_result(self, tmp_path, monkeypatch):
        """LN-21: incdirs -> -I pairs, extra_args appended, rtl files last; the
        subprocess result tuple is returned verbatim (174-178, 181-182)."""
        import soc_model.backends.lint as lint_mod

        captured = {}

        class _Result:
            returncode = 0
            stdout = "all good"
            stderr = ""

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return _Result()

        monkeypatch.setattr(lint_mod.subprocess, "run", _fake_run)
        be = lint_mod.SoCLintBackend(slang_bin="/fake/slang")
        rc, out, err = be._lint_cli(
            ["a.sv", "b.sv"],
            incdirs=["/inc1", "/inc2"],
            extra_args=["--lint-only"],
        )
        assert (rc, out, err) == (0, "all good", "")
        cmd = captured["cmd"]
        assert cmd[0] == "/fake/slang"
        # -I pairs precede extra_args, which precede the source files
        assert cmd[1:5] == ["-I", "/inc1", "-I", "/inc2"]
        assert "--lint-only" in cmd
        assert cmd[-2:] == ["a.sv", "b.sv"]
        # the call used pipes + a timeout (181)
        assert captured["kwargs"]["timeout"] == 300

    def test_cli_timeout_branch(self, tmp_path, monkeypatch):
        """LN-22: a subprocess.TimeoutExpired is caught and reported as
        (-1, '', 'slang timed out…') (187-188)."""
        import soc_model.backends.lint as lint_mod

        def _raise_timeout(cmd, **kwargs):
            raise lint_mod.subprocess.TimeoutExpired(cmd, 300)

        monkeypatch.setattr(lint_mod.subprocess, "run", _raise_timeout)
        be = lint_mod.SoCLintBackend(slang_bin="/fake/slang")
        rc, out, err = be._lint_cli(["x.sv"])
        assert rc == -1
        assert out == ""
        assert "timed out" in err

    def test_lint_and_report_cli_pass(self, tmp_path, monkeypatch, capsys):
        """LN-23: the CLI branch of lint_and_report (HAS_PYSLANG False) with a
        rc==0 fake prints stdout + PASS and returns True (235-241)."""
        import soc_model.backends.lint as lint_mod
        if lint_mod._HAS_PYSLANG:
            pytest.skip("pyslang installed: CLI branch of lint_and_report not taken")

        class _Result:
            returncode = 0
            stdout = "cli-stdout-line"
            stderr = "cli-stderr-line"

        monkeypatch.setattr(lint_mod.subprocess, "run",
                            lambda cmd, **kw: _Result())
        (tmp_path / "ok.sv").write_text("module ok; endmodule")
        be = lint_mod.SoCLintBackend(slang_bin="/fake/slang")
        assert be.lint_and_report(tmp_path) is True
        out = capsys.readouterr().out
        assert "Using slang CLI" in out
        assert "cli-stdout-line" in out    # stdout.strip() printed (235-236)
        assert "cli-stderr-line" in out    # stderr.strip() printed (237-238)
        assert "Lint PASSED." in out

    def test_lint_and_report_cli_fail_nonzero(self, tmp_path, monkeypatch, capsys):
        """LN-24: a non-zero (non -1) rc from the CLI -> 'Lint FAILED — slang
        exited with code N.' + return False (242-244)."""
        import soc_model.backends.lint as lint_mod
        if lint_mod._HAS_PYSLANG:
            pytest.skip("pyslang installed: CLI branch of lint_and_report not taken")

        class _Result:
            returncode = 3
            stdout = ""
            stderr = "error: syntax"

        monkeypatch.setattr(lint_mod.subprocess, "run",
                            lambda cmd, **kw: _Result())
        (tmp_path / "bad.sv").write_text("module bad")
        be = lint_mod.SoCLintBackend(slang_bin="/fake/slang")
        assert be.lint_and_report(tmp_path) is False
        out = capsys.readouterr().out
        assert "exited with code 3" in out

    def test_lint_and_report_cli_tool_absent_returns_false(self, tmp_path,
                                                           monkeypatch, capsys):
        """LN-25: rc == -1 (tool absent / FileNotFoundError) -> WARNING printed
        and return False, before any PASS/FAIL decision (232-234)."""
        import soc_model.backends.lint as lint_mod
        if lint_mod._HAS_PYSLANG:
            pytest.skip("pyslang installed: CLI branch of lint_and_report not taken")

        def _raise_fnf(cmd, **kwargs):
            raise FileNotFoundError(cmd[0])

        monkeypatch.setattr(lint_mod.subprocess, "run", _raise_fnf)
        (tmp_path / "x.sv").write_text("module x; endmodule")
        be = lint_mod.SoCLintBackend(slang_bin="/fake/slang")
        assert be.lint_and_report(tmp_path) is False
        assert "WARNING" in capsys.readouterr().out


class TestLintAndReportFlistBranch:
    """lint_and_report flist-collection branch (204-205, 211)."""

    def test_flist_branch_prints_and_collects(self, tmp_path, monkeypatch,
                                              capsys):
        """LN-26: providing flist_path + project_dir takes the collect_from_flist
        branch (204-205), prints the parse banner, the file count, and the
        include-directory count (211)."""
        import soc_model.backends.lint as lint_mod
        # Make the lint itself a no-op success regardless of pyslang presence,
        # so the test isolates the flist-collection + reporting branch.
        monkeypatch.setattr(lint_mod.SoCLintBackend, "_lint_pyslang",
                            lambda self, files, incdirs=None: (0, 0, ""))
        monkeypatch.setattr(lint_mod.SoCLintBackend, "_lint_cli",
                            lambda self, files, incdirs=None, extra_args=None:
                                (0, "", ""))
        (tmp_path / "top.sv").write_text("module top; endmodule")
        (tmp_path / "inc").mkdir()
        flist = tmp_path / "d.flist"
        flist.write_text("+incdir+inc\ntop.sv\n")
        be = lint_mod.SoCLintBackend(slang_bin="/fake/slang")
        ok = be.lint_and_report(tmp_path, project_dir=str(tmp_path),
                                flist_path=str(flist))
        assert ok is True
        out = capsys.readouterr().out
        assert f"Parsing filelist: {flist}" in out      # 204
        assert "Found 1 RTL file(s) to lint" in out      # 209
        assert "Include directories: 1" in out           # 210-211


# ===========================================================================
# html.py  — SoCVisualizer  (HT-01..07)  — SMOKE ONLY (no golden)
# ===========================================================================

class TestHtmlBackend:
    def test_generate_html_nonempty(self, fixtures_dir, tmp_path):
        """HT-01/02: generate_html writes a large, valid HTML document with the
        DOCTYPE and a <title> carrying the top name."""
        from soc_model.backends.html import SoCVisualizer
        top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
        out = tmp_path / "soc.html"
        SoCVisualizer(top).generate_html(str(out))
        html = out.read_text()
        assert len(html) > 1000
        assert "<!DOCTYPE html>" in html
        assert f"<title>{top.name}" in html

    def test_validation_messages_embedded(self, fixtures_dir, tmp_path):
        """HT-03: validation messages are stringified into the page; the None
        default is safe (no crash)."""
        from soc_model.backends.html import SoCVisualizer
        from soc_model.validator import ValidationMessage
        top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
        msg = ValidationMessage("warning", "reference", "u_x", "p", "demo message")
        out = tmp_path / "soc.html"
        SoCVisualizer(top).generate_html(str(out), validation_messages=[msg])
        html = out.read_text()
        assert "demo message" in html
        # None default path also writes without raising
        out2 = tmp_path / "soc2.html"
        SoCVisualizer(top).generate_html(str(out2), validation_messages=None)
        assert out2.exists()

    def test_embedded_json_parses(self, fixtures_dir, tmp_path):
        """HT-04: the embedded graphData / validationMessages JSON literals parse.

        graphData is ``json.dumps(..., indent=2)`` (multi-line) terminated by a
        line ``};``; validationMessages is single-line ``json.dumps(messages)``.
        """
        from soc_model.backends.html import SoCVisualizer
        top = build_module_from_yaml(fixtures_dir, "two_region_soc.yaml")
        out = tmp_path / "soc.html"
        SoCVisualizer(top).generate_html(str(out))
        html = out.read_text()

        # graphData: capture from the '{' after the marker up to the closing
        # '\n};' that terminates the json.dumps(indent=2) literal.
        marker = "const graphData = "
        idx = html.find(marker)
        assert idx != -1, "graphData literal not found in HTML"
        start = idx + len(marker)
        end = html.find("\n};", start)
        assert end != -1, "graphData terminator not found"
        literal = html[start:end + 2]   # include the closing brace
        parsed = json.loads(literal)
        assert isinstance(parsed, dict)
        assert "nodes" in parsed and "links" in parsed

        # validationMessages is a single-line array literal.
        vmarker = "const validationMessages = "
        vidx = html.find(vmarker)
        assert vidx != -1, "validationMessages literal not found in HTML"
        vstart = vidx + len(vmarker)
        vend = html.find("\n", vstart)
        vliteral = html[vstart:vend].rstrip().rstrip(";")
        vparsed = json.loads(vliteral)
        assert isinstance(vparsed, list)

    def test_two_runs_byte_identical(self, fixtures_dir, tmp_path):
        """HT-05: html.py has no datetime/random -> two runs are byte-identical."""
        from soc_model.backends.html import SoCVisualizer
        top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
        a = tmp_path / "a.html"
        b = tmp_path / "b.html"
        SoCVisualizer(top).generate_html(str(a))
        SoCVisualizer(top).generate_html(str(b))
        assert a.read_bytes() == b.read_bytes()

    def test_empty_module_no_crash(self, tmp_path):
        """HT-06: a bare Module (no instances/interconnects) still renders valid
        HTML without raising."""
        from soc_model.backends.html import SoCVisualizer
        out = tmp_path / "bare.html"
        SoCVisualizer(Module(name="bare", gen=True)).generate_html(str(out))
        html = out.read_text()
        assert "<!DOCTYPE html>" in html
        assert "<title>bare" in html

    def test_classify_link_type_branches(self):
        """HT-07: _classify_link_type maps iface types / name heuristics to the
        ahb/apb/axis/clock/reset/wire link categories."""
        from soc_model.backends.html import SoCVisualizer
        be = SoCVisualizer(Module(name="t"))
        ics = {"t_interconnect"}
        assert be._classify_link_type("p", "x", "ahb", ics) == "ahb"
        assert be._classify_link_type("p", "x", "apb", ics) == "apb"
        assert be._classify_link_type("p", "x", "axis", ics) == "axis"
        assert be._classify_link_type("p", "x", "swd", ics) == "wire"
        # interconnect-target connection -> ahb
        assert be._classify_link_type("p", "t_interconnect.foo", "wire", ics) == "ahb"
        # name heuristics
        assert be._classify_link_type("sys_hclk", "n", "wire", ics) == "clock"


# ===========================================================================
# html.py  — SoCVisualizer data-builder gap-fill  (HT-08..10)
#
# HT-01..07 above are smoke (already 78% > the 30% target). These three
# exercise the remaining data-shaping branches that smoke does not reach —
# the address-decode register-table emission (html.py:403-458), the
# per-initiator visibility/remap window arm (545-557), and the nested
# child-interconnect base-address recursion (583-589). They assert the
# concrete dict structure (names, absolute addresses, remap bits), NOT the
# rendered HTML, so they are meaningful behaviour checks rather than
# coverage padding. All built from in-memory dataclasses — no fixtures,
# no EDA tool, no ARM_IP_LIBRARY_PATH.
# ===========================================================================

class TestHtmlBackendDataBuilders:
    def test_address_map_emits_decode_registers_and_nested(self):
        """HT-08: a target instance carrying an address_decode is walked into a
        flat region list (html.py:403-458). The resolved register map is
        emitted with absolute register/field addresses, and a nested decode
        becomes a deeper-depth child region.

        Drives _build_address_map -> _collect_module_regions ->
        _collect_decode_regions (incl. the resolved_register_map and nested
        address_decode arms).
        """
        from soc_model.backends.html import SoCVisualizer

        fld = RegisterField(name="EN", bits="0", access="RW", desc="enable")
        reg = Register(name="CTRL", offset=0x4, width=32, access="RW",
                       desc="control", fields=[fld])
        rm = RegisterMap(name="blk_regs", module="blk_regs", registers=[reg])

        inner_slot = AddressDecodeSlot(slot=0, name="inner", module="inner_mod",
                                       offset=0x10, size=0x10, desc="nested")
        inner_ad = AddressDecode(type="apb_slave_mux", slots=[inner_slot])
        slot = AddressDecodeSlot(slot=0, name="ctrl_blk", module="ctrl_mod",
                                 offset=0x100, size=0x100,
                                 resolved_register_map=rm, desc="ctrl",
                                 address_decode=inner_ad)
        ad = AddressDecode(type="ahb_slave_mux", slots=[slot])

        child = Module(name="periph_ss", gen=True)
        child.address_decode = ad
        inst = Instance(instance_name="u_periph", module_name="periph_ss",
                        addressable=True, resolved_module=child)

        top = Module(name="top_soc", gen=True)
        top.instances = [inst]
        tgt = InterconnectTarget(name="periph", instance="u_periph",
                                 base=0x40000000, size=0x1000, sw_access="rw",
                                 region_type="peripheral")
        ic = Interconnect(name="ahb_mtx", type="ahb_lite", targets=[tgt],
                          initiators=[InterconnectInitiator(
                              name="cpu",
                              targets=[InterconnectInitiatorTarget(name="periph")])])
        top.interconnects = [ic]

        regions = SoCVisualizer(top)._build_address_map()
        decode = [r for r in regions if r["region_type"] == "decode"]
        by_name = {r["name"]: r for r in decode}
        assert {"ctrl_blk", "inner"} <= set(by_name)

        ctrl = by_name["ctrl_blk"]
        # abs_base = parent target base (0x40000000) + slot offset (0x100)
        assert ctrl["abs_base"] == 0x40000100
        assert ctrl["instance"] == "ctrl_mod"
        assert ctrl["subordinate_bus"] is False
        # register map name + the single register's absolute address (0x...104)
        assert ctrl["register_map"] == "blk_regs"
        assert len(ctrl["registers"]) == 1
        creg = ctrl["registers"][0]
        assert creg["name"] == "CTRL"
        assert creg["abs_addr"] == 0x40000104
        assert creg["access"] == "RW"
        # field projection (the inner list-comprehension arm, 419-427)
        assert creg["fields"] == [
            {"name": "EN", "bits": "0", "access": "RW", "desc": "enable"}
        ]

        # nested decode: deeper depth + abs_base = ctrl abs_base + inner offset
        inner = by_name["inner"]
        assert inner["abs_base"] == 0x40000110
        assert inner["depth"] == ctrl["depth"] + 1
        assert inner["register_map"] is None
        assert inner["registers"] == []
        # the nested region is also attached as a child of ctrl_blk
        assert [c["name"] for c in ctrl["children"]] == ["inner"]

    def test_interconnect_info_visibility_remap_windows(self):
        """HT-09: a per-initiator target with two visibility windows produces
        two windows — one remapping (remap_bit/behaviour populated) and one
        plain (None/None) — while a target without visibility yields a single
        default window (html.py:544-565).
        """
        from soc_model.backends.html import SoCVisualizer

        remap_vis = [
            {"base": 0x0,
             "remap": {"remapping": True, "remap_bit": 2,
                       "remap_behaviour": "alias"}},
            {"base": 0x80000000, "remap": {"remapping": False}},
        ]
        t_mem = InterconnectTarget(name="mem", base=0x10000000, size=0x2000,
                                   sw_access="rw")
        t_io = InterconnectTarget(name="io", base=0x40000000, size=0x1000,
                                  sw_access="rw")
        init = InterconnectInitiator(name="cpu", targets=[
            InterconnectInitiatorTarget(name="mem", visibility=remap_vis),
            InterconnectInitiatorTarget(name="io"),     # no visibility
        ])
        top = Module(name="top_soc", gen=True)
        top.interconnects = [Interconnect(name="top_mtx", type="ahb_lite",
                                          targets=[t_mem, t_io],
                                          initiators=[init])]

        info = SoCVisualizer(top)._build_interconnect_info()
        top_ic = next(i for i in info if i["name"] == "top_mtx")
        assert top_ic["top_level"] is True
        assert top_ic["path"] == "top_mtx"

        cpu = top_ic["initiators"][0]
        mem = next(t for t in cpu["targets"] if t["name"] == "mem")
        assert mem["default_base"] == 0x10000000
        # two windows: the remapping one carries bit/behaviour, the other Nones
        assert len(mem["windows"]) == 2
        remap_w = next(w for w in mem["windows"] if w["remap_bit"] is not None)
        plain_w = next(w for w in mem["windows"] if w["remap_bit"] is None)
        assert remap_w["base"] == 0x0
        assert remap_w["remap_bit"] == 2
        assert remap_w["remap_behaviour"] == "alias"
        assert plain_w["base"] == 0x80000000
        assert plain_w["remap_behaviour"] is None

        # the no-visibility target falls into the single-default-window arm
        io = next(t for t in cpu["targets"] if t["name"] == "io")
        assert io["windows"] == [{
            "base": 0x40000000, "size": 0x1000,
            "remap_bit": None, "remap_behaviour": None,
        }]

    def test_interconnect_info_recurses_into_child_base(self):
        """HT-10: a child module with its own interconnect is recursed into,
        and its parent_base is derived from the owning target's base address
        (html.py:580-593). The child window's default_base = child base +
        child target base.
        """
        from soc_model.backends.html import SoCVisualizer

        sub = Module(name="sub_ss", gen=True)
        sub_tgt = InterconnectTarget(name="sub_ram", base=0x1000, size=0x400,
                                     sw_access="rw")
        sub.interconnects = [Interconnect(
            name="sub_mtx", type="ahb_lite", targets=[sub_tgt],
            initiators=[InterconnectInitiator(
                name="sub_cpu",
                targets=[InterconnectInitiatorTarget(name="sub_ram")])])]
        sub_inst = Instance(instance_name="u_sub", module_name="sub_ss",
                            addressable=True, resolved_module=sub)

        top = Module(name="top_soc", gen=True)
        top.instances = [sub_inst]
        t_sub = InterconnectTarget(name="sub", instance="u_sub",
                                   base=0x20000000, size=0x10000, sw_access="rw")
        top.interconnects = [Interconnect(
            name="top_mtx", type="ahb_lite", targets=[t_sub],
            initiators=[InterconnectInitiator(
                name="cpu",
                targets=[InterconnectInitiatorTarget(name="sub")])])]

        info = SoCVisualizer(top)._build_interconnect_info()
        names = {i["name"] for i in info}
        assert {"top_mtx", "sub_mtx"} <= names

        sub_info = next(i for i in info if i["name"] == "sub_mtx")
        # parent_base picked up from the owning target's base (0x20000000)
        assert sub_info["parent_base"] == 0x20000000
        assert sub_info["top_level"] is False
        assert sub_info["path"] == "u_sub.sub_mtx"
        # child target default_base = parent_base + child target base
        sub_window = sub_info["initiators"][0]["targets"][0]["windows"][0]
        assert sub_window["base"] == 0x20001000


# ===========================================================================
# protocol_utils.py  — pure helpers  (PU-01..14)
# ===========================================================================

class TestProtocolUtils:
    # --- width_str (PU-05..08) ------------------------------------------
    def test_width_str_int(self):
        """PU-05: int 1 -> '' (1-bit, no range); N>1 -> '[N-1:0]'."""
        from soc_model.backends.protocol_utils import width_str
        assert width_str(1) == ""
        assert width_str(8) == "[7:0]"
        assert width_str(32) == "[31:0]"

    def test_width_str_numeric_string(self):
        """PU-06: a numeric string is parsed as an int; '1' -> '', '16' ->
        '[15:0]'. A leading '$' on a numeric is stripped before parsing."""
        from soc_model.backends.protocol_utils import width_str
        assert width_str("1") == ""
        assert width_str("16") == "[15:0]"
        assert width_str("$8") == "[7:0]"     # $-stripped then parsed

    def test_width_str_non_numeric_param(self):
        """PU-07: a non-numeric (param) string -> '[<clean>-1:0]' with the '$'
        stripped — the SV declaration for a parameterised width."""
        from soc_model.backends.protocol_utils import width_str
        assert width_str("$W") == "[W-1:0]"
        assert width_str("$SYS_ADDR_W") == "[SYS_ADDR_W-1:0]"
        assert width_str("WIDTH") == "[WIDTH-1:0]"

    def test_width_str_other_type(self):
        """PU-08: a non-int / non-str argument (None) -> '' (final return)."""
        from soc_model.backends.protocol_utils import width_str
        assert width_str(None) == ""
        assert width_str([1, 2]) == ""

    # --- param_width_str (PU-09) ---------------------------------------
    def test_param_width_str_mirrors_width_str(self):
        """PU-09: param_width_str is behaviourally identical to width_str across
        int / numeric-string / param-string / other-type inputs."""
        from soc_model.backends.protocol_utils import param_width_str, width_str
        for arg in (1, 8, 32, "1", "16", "$8", "$SYS_ADDR_W", "WIDTH", None, []):
            assert param_width_str(arg) == width_str(arg)
        # spot-check the param-string branch (line 191) explicitly
        assert param_width_str("$SYS_DATA_W") == "[SYS_DATA_W-1:0]"
        assert param_width_str(16) == "[15:0]"

    # --- bus_member_names (PU-10/13/14) --------------------------------
    def test_bus_member_names_ahb(self):
        """PU-10: an AHB iface expands to the sorted union of TARGET + INITIATOR
        signal suffixes as '<name>_<sig>' — covers hsel (target-only),
        hreadyout (target-only) and haddr (both)."""
        from soc_model.backends.protocol_utils import bus_member_names
        names = bus_member_names(Interface(name="b", type="ahb"))
        assert names == sorted(names)               # union is sorted
        assert "b_hsel" in names                    # target-only suffix
        assert "b_hreadyout" in names               # target-only suffix
        assert "b_haddr" in names                   # present on both boundaries
        assert "b_hwdata" in names and "b_hrdata" in names

    def test_bus_member_names_axis(self):
        """PU-13: an AXIS iface hits the non-AHB axis branch (line 123-124),
        expanding to the AXIS_SIGNALS suffixes; 'axis_stream' is an alias."""
        from soc_model.backends.protocol_utils import bus_member_names
        axis = bus_member_names(Interface(name="s", type="axis"))
        assert axis == ["s_tvalid", "s_tready", "s_tdata", "s_tstrb", "s_tlast"]
        # the alias resolves to the same member set
        assert bus_member_names(Interface(name="s", type="axis_stream")) == axis

    def test_bus_member_names_apb_expands(self):
        """PU-14 (updated): `apb` now has a registered protocol (ApbProtocol),
        so an APB bundle expands to its per-signal member names instead of
        silently returning [] — the [] behaviour is what let APB sub-signal
        references (e.g. an APB completer's PREADY) go unrecognised and
        unbound. Plain wires still expand to nothing."""
        from soc_model.backends.protocol_utils import bus_member_names
        names = bus_member_names(Interface(name="p", type="apb"))
        assert names == sorted(names)               # bus union is sorted
        for suffix in ('psel', 'penable', 'paddr', 'pwrite', 'pwdata',
                       'prdata', 'pready', 'pslverr'):
            assert f"p_{suffix}" in names
        assert bus_member_names(Interface(name="w", type="wire")) == []

    # --- passthrough_initiator_uses_initiator_boundary (PU-01..04) -----
    def test_passthrough_initiator_non_dict_true(self):
        """PU-01: a non-dict iface_params -> True (initiator boundary)."""
        from soc_model.backends.protocol_utils import (
            passthrough_initiator_uses_initiator_boundary as boundary)
        assert boundary(None) is True
        assert boundary("x") is True
        assert boundary([]) is True

    def test_passthrough_initiator_exclude_hready_false(self):
        """PU-02/03: a dict without hready in EXCLUDE -> True (initiator
        boundary); with hready in EXCLUDE -> False (slave/target boundary).
        Covers line 162-164."""
        from soc_model.backends.protocol_utils import (
            passthrough_initiator_uses_initiator_boundary as boundary)
        assert boundary({}) is True
        assert boundary({"EXCLUDE": ["hburst"]}) is True
        assert boundary({"EXCLUDE": ["hready"]}) is False
        # PU-04: EXCLUDE explicitly None -> `or []` guard -> True
        assert boundary({"EXCLUDE": None}) is True


def test_backends_misc_modules_import():
    """Regression guard: every misc backend module imports cleanly."""
    for name in ("python", "sram", "build_info", "system", "chip", "lint", "html"):
        mod = importlib.import_module(f"soc_model.backends.{name}")
        assert mod is not None


# ===========================================================================
# AHB backend gap-fill (unit tier, runs by default).
#
# soc_model/backends/ahb.py is tooling-heavy: the perl path and the APB /
# remap / multi-initiator helpers were only reached by the deselected
# `tooling` tier (tests/tooling/test_ahb_perl.py). The tests below drive
# those helpers purely in-memory (hand-built Interconnect dataclasses) so the
# coverage lands in the DEFAULT run with no EDA tool, no perl, and no
# ARM_IP_LIBRARY_PATH. The single perl test fakes BuildBusMatrix.pl inside
# tmp_path and monkeypatches subprocess.run — no vendor IP tree is ever
# touched.
# ===========================================================================

from soc_model.backends.ahb import (  # noqa: E402
    SoCAhbBackend, _build_apb_slot_list, _build_apb_target_context,
)


def _apb_target_multislot():
    """An APB InterconnectTarget carrying a 2-slave apb_config + big-endian.

    Exercises the with-slaves (has_mux) branch of _build_apb_target_context
    and the slot-config/big-endian branches of _build_sv_context /
    _build_pkg_context.
    """
    apb_cfg = {
        "big_endian_support": True,
        "slaves": [
            {"slot": 0, "name": "timer0", "comment": "Timer 0",
             "config": {"TIMER0_BASE": "0x40000000"}},
            {"slot": 1, "name": "uart0", "enabled": False,
             "config": {"UART0_BASE": "0x40001000"}},
        ],
    }
    return InterconnectTarget(name="periph", base=0x40000000,
                             protocol="apb", apb_config=apb_cfg)


def _remap_interconnect():
    """A single-initiator interconnect whose target is reached via two
    visibility windows: one MOVE-remap window (remappable bit 0) and one
    ALIAS window. Drives the visibility / remap-entry / address-region paths
    of _build_initiator_data, plus the remap-config and effective-map logic.
    """
    ic = Interconnect(name="rmp_ahb_interconnect", gen=True)
    ic.targets = [
        InterconnectTarget(name="sram_0", base=0x0, size=0x1000),
        InterconnectTarget(name="boot", base=0x10000000, size=0x1000),
    ]
    boot_vis = InterconnectInitiatorTarget(
        name="boot",
        visibility=[
            # remappable window (becomes a remap_region, bit 0)
            {"base": 0x0, "aperture": 0x1000,
             "remap": {"remapping": True, "remap_bit": 0,
                       "remap_behaviour": "move"}},
            # plain alias window (becomes an address_region, remapping='alias')
            {"base": 0x10000000, "aperture": 0x1000,
             "remap_behaviour": "alias"},
        ],
    )
    sram_vis = InterconnectInitiatorTarget(name="sram_0")  # no visibility
    ic.initiators = [
        InterconnectInitiator(name="cpu_m", targets=[boot_vis, sram_vis])
    ]
    return ic


# ---------------------------------------------------------------------------
# AHB-GF-01  _build_apb_slot_list — multiple declared slaves expand correctly
# ---------------------------------------------------------------------------
class TestApbSlotListMulti:
    def test_two_declared_slaves_rest_gaps(self):
        slots = _build_apb_slot_list(
            {"slaves": [{"slot": 0, "name": "timer0"},
                        {"slot": 3, "name": "uart0", "enabled": False,
                         "comment": "Console"}]}
        )
        assert len(slots) == 16
        # declared slot 0
        assert slots[0]["name"] == "timer0"
        assert slots[0]["is_gap"] is False
        assert slots[0]["enabled"] is True
        assert slots[0]["param_name"] == "TIMER0"
        # declared slot 3 explicitly disabled, with a comment passed through
        assert slots[3]["name"] == "uart0"
        assert slots[3]["is_gap"] is False
        assert slots[3]["enabled"] is False
        assert slots[3]["comment"] == "Console"
        assert slots[3]["param_name"] == "UART0"
        # an undeclared slot in between is an auto-gap
        assert slots[2]["is_gap"] is True
        assert slots[2]["enabled"] is False
        assert slots[2]["comment"] == "Reserved"
        assert slots[2]["param_name"] == "GAP_2"


# ---------------------------------------------------------------------------
# AHB-GF-02  _build_apb_target_context — with-slaves (has_mux) path:
#            decode window, n_slots and per-slot ahb_addr / paddr_offset
# ---------------------------------------------------------------------------
class TestApbTargetContextMulti:
    def test_with_slaves_addresses_computed(self):
        ctx = _build_apb_target_context(_apb_target_multislot())
        assert ctx["has_mux"] is True
        assert ctx["big_endian"] is True
        # default decode for a mux'd target: msb 15 .. lsb 12 -> 16 slots of 4K
        assert ctx["decode_msb"] == 15
        assert ctx["decode_lsb"] == 12
        assert ctx["n_slots"] == 16
        assert ctx["slot_size"] == "0x1000"
        assert ctx["region_base"] == "0x40000000"
        # per-slot absolute + offset addresses (slot_size = 1 << decode_lsb)
        assert ctx["slots"][0]["ahb_addr"] == "0x40000000"
        assert ctx["slots"][0]["paddr_offset"] == "0x00000000"
        assert ctx["slots"][1]["ahb_addr"] == "0x40001000"
        assert ctx["slots"][1]["paddr_offset"] == "0x00001000"


# ---------------------------------------------------------------------------
# AHB-GF-03  _build_initiator_data — visibility windows split into
#            remap_regions (remappable) vs address_regions (alias)
# ---------------------------------------------------------------------------
class TestInitiatorDataVisibility:
    def test_visibility_splits_remap_and_alias(self):
        be = SoCAhbBackend(Module(name="rmp"))
        data = be._build_initiator_data(_remap_interconnect())
        cpu = data["cpu_m"]
        # both targets are connected
        assert set(cpu["connections"]) == {"boot", "sram_0"}

        # the remappable visibility window became a remap_region on bit 0
        assert len(cpu["remap_regions"]) == 1
        rr = cpu["remap_regions"][0]
        assert rr["interface"] == "boot"
        assert rr["bit"] == 0
        assert rr["mem_lo"] == "00000000"
        assert rr["mem_hi"] == "00000fff"

        # address_regions: sram_0 (no visibility -> 'none') + boot alias window
        ar = {r["interface"]: r for r in cpu["address_regions"]}
        assert ar["sram_0"]["remapping"] == "none"
        assert ar["sram_0"]["mem_lo"] == "00000000"
        assert ar["sram_0"]["mem_hi"] == "00000fff"
        assert ar["boot"]["remapping"] == "alias"
        assert ar["boot"]["mem_lo"] == "10000000"
        assert ar["boot"]["mem_hi"] == "10000fff"

    def test_unknown_initiator_target_skipped(self):
        be = SoCAhbBackend(Module(name="rmp"))
        ic = _remap_interconnect()
        ic.initiators[0].targets.append(
            InterconnectInitiatorTarget(name="does_not_exist"))
        data = be._build_initiator_data(ic)
        # the dangling reference is continue-skipped, not connected
        assert "does_not_exist" not in data["cpu_m"]["connections"]
        ifaces = {r["interface"] for r in data["cpu_m"]["address_regions"]}
        assert "does_not_exist" not in ifaces


# ---------------------------------------------------------------------------
# AHB-GF-04  _generate_remap_configurations + _compute_effective_address_map:
#            the remap bit toggles the effective view, MOVE windows are
#            suppressed when their remap is active.
# ---------------------------------------------------------------------------
class TestRemapAndEffectiveMap:
    def test_one_bit_two_configs(self):
        be = SoCAhbBackend(Module(name="rmp"))
        data = be._build_initiator_data(_remap_interconnect())
        configs = be._generate_remap_configurations(data)
        assert configs == [{0: False}, {0: True}]

    def test_effective_map_bit_off_uses_alias_region(self):
        be = SoCAhbBackend(Module(name="rmp"))
        data = be._build_initiator_data(_remap_interconnect())
        eff = be._compute_effective_address_map(data["cpu_m"], {0: False})
        srcs = {(r["interface"], r["source"]) for r in eff}
        # remap inactive: no remap_region present; both address_regions show
        assert ("boot", "remap_region") not in srcs
        assert ("boot", "address_region") in srcs
        assert ("sram_0", "address_region") in srcs
        # sorted by mem_lo ascending
        assert [r["mem_lo"] for r in eff] == sorted(r["mem_lo"] for r in eff)

    def test_effective_map_bit_on_activates_remap_region(self):
        be = SoCAhbBackend(Module(name="rmp"))
        data = be._build_initiator_data(_remap_interconnect())
        eff = be._compute_effective_address_map(data["cpu_m"], {0: True})
        srcs = {(r["interface"], r["source"]) for r in eff}
        # remap active: the boot remap_region appears at 0x0
        assert ("boot", "remap_region") in srcs
        remap = next(r for r in eff if r["source"] == "remap_region")
        assert remap["mem_lo"] == "00000000"
        assert remap["remapping"] == "remap"

    def test_move_window_suppressed_when_remap_active(self):
        """A MOVE address_region is dropped when a remap for the same
        interface is active (ahb.py:533-540)."""
        be = SoCAhbBackend(Module(name="mv"))
        ic = Interconnect(name="mv_ahb_interconnect", gen=True)
        ic.targets = [InterconnectTarget(name="boot", base=0x10000000,
                                         size=0x1000)]
        ic.initiators = [InterconnectInitiator(
            name="cpu_m",
            targets=[InterconnectInitiatorTarget(
                name="boot",
                visibility=[
                    {"base": 0x0, "aperture": 0x1000,
                     "remap": {"remapping": True, "remap_bit": 0,
                               "remap_behaviour": "move"}},
                    {"base": 0x10000000, "aperture": 0x1000,
                     "remap_behaviour": "move"},
                ])])]
        data = be._build_initiator_data(ic)
        # bit off: only the MOVE address_region (no remap_region)
        off = be._compute_effective_address_map(data["cpu_m"], {0: False})
        assert [r["source"] for r in off] == ["address_region"]
        assert off[0]["remapping"] == "move"
        # bit on: the address_region is suppressed, only the remap_region left
        on = be._compute_effective_address_map(data["cpu_m"], {0: True})
        assert [r["source"] for r in on] == ["remap_region"]


# ---------------------------------------------------------------------------
# AHB-GF-05  _build_sv_context — APB target flat param list (per-slot ENABLE
#            + big-endian BE param) and the all_targets / apb_targets split.
# ---------------------------------------------------------------------------
class TestSvContextApbParams:
    def test_apb_slot_and_be_params(self):
        ic = Interconnect(name="p_ahb_interconnect", gen=True)
        ic.targets = [
            InterconnectTarget(name="sram_0", base=0x0, size=0x1000),
            _apb_target_multislot(),
        ]
        ic.initiators = [InterconnectInitiator(
            name="cpu_m",
            targets=[InterconnectInitiatorTarget(name="sram_0"),
                     InterconnectInitiatorTarget(name="periph")])]
        ctx = SoCAhbBackend(Module(name="p"))._build_sv_context(
            ic, "p", 32, 32)

        # AHB vs APB split
        assert ctx["ahb_targets"] == ["sram_0"]
        assert [t["name"] for t in ctx["apb_targets"]] == ["periph"]
        # only non-gap slots become ENABLE params (timer0 enabled, uart0 not)
        names = {p["name"]: p["value"] for p in ctx["apb_params"]}
        assert names["PERIPH_TIMER0_ENABLE"] == 1
        assert names["PERIPH_UART0_ENABLE"] == 0
        # big-endian target contributes a *_BE param
        assert "PERIPH_BE" in names
        # the single APB target is flagged as the last target
        assert ctx["apb_targets"][0]["is_last_target"] is True


# ---------------------------------------------------------------------------
# AHB-GF-06  _build_pkg_context — APB slot config dicts are merged into the
#            target's `config` entry.
# ---------------------------------------------------------------------------
class TestPkgContextApbSlotConfig:
    def test_slot_configs_merged(self):
        ic = Interconnect(name="p_ahb_interconnect", gen=True)
        ic.targets = [_apb_target_multislot()]
        ctx = SoCAhbBackend(Module(name="p"))._build_pkg_context(ic, "p", 32)
        info = {t["name"]: t for t in ctx["all_target_info"]}
        cfg = info["periph"]["config"]
        # both slaves' config dicts are merged into one
        assert cfg == {"TIMER0_BASE": "0x40000000",
                       "UART0_BASE": "0x40001000"}

    def test_apb_target_without_slot_config_is_none(self):
        ic = Interconnect(name="p_ahb_interconnect", gen=True)
        ic.targets = [InterconnectTarget(
            name="periph", base=0x40000000, protocol="apb",
            apb_config={"slaves": [{"slot": 0, "name": "timer0"}]})]
        ctx = SoCAhbBackend(Module(name="p"))._build_pkg_context(ic, "p", 32)
        # no slave carries a `config` -> entry stays None
        assert ctx["all_target_info"][0]["config"] is None


# ---------------------------------------------------------------------------
# AHB-GF-07  _run_build_bus_matrix — perl result-handling branches.
#
# CRITICAL SAFETY: a FAKE BuildBusMatrix.pl is created under tmp_path (NEVER
# the read-only vendor IP tree) and subprocess.run is monkeypatched, so no
# real perl runs and ARM_IP_LIBRARY_PATH is never read/written.
# ---------------------------------------------------------------------------
class TestRunBuildBusMatrix:
    class _FakeResult:
        def __init__(self, rc):
            self.returncode = rc
            self.stdout = "fake stdout"
            self.stderr = "fake stderr"

    def _backend_with_fake_script(self, tmp_path):
        ip = tmp_path / "ip"
        script = (ip / "latest" / "Corstone-101" / "logical"
                  / "cmsdk_ahb_busmatrix" / "bin" / "BuildBusMatrix.pl")
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("# fake perl script\n")
        be = SoCAhbBackend(Module(name="m"), arm_ip_library_path=str(ip))
        ic = Interconnect(name="m_ahb_interconnect", gen=True)
        return be, ic

    def test_success_writes_log_with_streams(self, tmp_path, monkeypatch):
        be, ic = self._backend_with_fake_script(tmp_path)
        logs = tmp_path / "logs"
        logs.mkdir()
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **k: self._FakeResult(0))
        be._run_build_bus_matrix(ic, tmp_path, tmp_path, tmp_path, logs)
        log = (logs / "m_ahb_interconnect.log").read_text()
        assert "Command:" in log
        assert "=== STDOUT ===" in log
        assert "fake stdout" in log
        assert "=== STDERR ===" in log
        assert "fake stderr" in log

    def test_nonzero_returncode_warns_not_raises(self, tmp_path, monkeypatch):
        be, ic = self._backend_with_fake_script(tmp_path)
        logs = tmp_path / "logs"
        logs.mkdir()
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **k: self._FakeResult(7))
        # exit code != 0 -> warns, writes the log, does NOT raise
        be._run_build_bus_matrix(ic, tmp_path, tmp_path, tmp_path, logs)
        assert (logs / "m_ahb_interconnect.log").is_file()

    def test_unset_env_skips_without_subprocess(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ARM_IP_LIBRARY_PATH", raising=False)
        be = SoCAhbBackend(Module(name="m"), arm_ip_library_path=None)
        ic = Interconnect(name="m_ahb_interconnect", gen=True)

        def _boom(*a, **k):
            raise AssertionError("subprocess.run must not run when "
                                 "ARM_IP_LIBRARY_PATH is unset")

        monkeypatch.setattr(subprocess, "run", _boom)
        be._run_build_bus_matrix(ic, tmp_path, tmp_path, tmp_path, tmp_path)

    def test_filenotfound_and_timeout_caught(self, tmp_path, monkeypatch):
        be, ic = self._backend_with_fake_script(tmp_path)
        logs = tmp_path / "logs"
        logs.mkdir()

        def _fnf(*a, **k):
            raise FileNotFoundError("perl: not found")

        monkeypatch.setattr(subprocess, "run", _fnf)
        be._run_build_bus_matrix(ic, tmp_path, tmp_path, tmp_path, logs)

        def _timeout(*a, **k):
            raise subprocess.TimeoutExpired(cmd="perl", timeout=120)

        monkeypatch.setattr(subprocess, "run", _timeout)
        be._run_build_bus_matrix(ic, tmp_path, tmp_path, tmp_path, logs)
