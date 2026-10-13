# Copyright 2026, SoC Labs (www.soclabs.org)
"""Golden + unit tests for ``soc_model/backends/discovery.py``.

The discovery backend (:class:`SoCDiscoveryBackend`) emits, for every
``gen: True`` interconnect, a read-only ``RegisterMap`` whose register
*reset values* encode the bus topology — the initiators, targets and their
addresses that runtime firmware / tooling reads back. ``generate(build_dir)``
writes ``discovery/<ic>_discovery.yaml`` and returns the
``(ic_name, register_map, yaml_path)`` tuples.

The YAML is the consumed contract: a top-level ``register_map`` key with
``name`` / ``module`` / ``gen`` / ``description`` / ``address_width`` (12) /
``data_width`` (32) / ``registers[]`` — each register carrying ``name`` /
``offset`` / ``width`` / ``access`` / ``reset_value`` (and ``fields`` where
present). The encoded topology (initiators / targets / addresses) lives in the
register reset values, exactly as the discovery-table reader decodes them.

Tier split:
  * ``unit``  — the pure encoders (``_encode_name`` / region-type / sw-access /
    protocol / visibility mask), header/packing reset values, register-count
    invariant, gen-filter, child recursion, and the YAML offset/reset format.
  * ``golden`` — byte-stable snapshot of the rendered ``.yaml`` for a hermetic
    fixture (``# Generated:`` line stripped by the shared helper).

Covers TEST_PLAN section 5.9 (DY-01..DY-14).
"""

from pathlib import Path

import pytest
import yaml

from soc_model.backends.discovery import (
    SoCDiscoveryBackend,
    _PROTOCOL_MAP,
    _REGION_TYPE_MAP,
    _SW_ACCESS_MAP,
    _TABLE_MAGIC,
    _TABLE_VERSION,
    _build_visibility_mask,
    _encode_name,
    _encode_protocol,
    _encode_region_type,
    _encode_sw_access,
)
from soc_model.model import (
    Instance,
    Interconnect,
    InterconnectInitiator,
    InterconnectInitiatorTarget,
    InterconnectTarget,
    Module,
)

from tests.conftest import SNAPSHOTS
from tests.helpers import assert_matches_snapshot, build_module_from_yaml


# ---------------------------------------------------------------------------
# Inline-model helpers (precise control over targets / initiators / params)
# ---------------------------------------------------------------------------

def _ic(name="ic0", *, gen=True, params=None, targets=None, initiators=None):
    ic = Interconnect(name=name, gen=gen, params=params or {})
    ic.targets = targets or []
    ic.initiators = initiators or []
    return ic


def _tgt(name, base=0, size=0, **kw):
    return InterconnectTarget(name=name, base=base, size=size, **kw)


def _init(name, target_names):
    return InterconnectInitiator(
        name=name,
        targets=[InterconnectInitiatorTarget(n) for n in target_names],
    )


def _regs_by_name(rm):
    return {r.name: r for r in rm.registers}


def _two_target_ic():
    """2 targets (memory/ahb + periph/apb), 1 initiator seeing both, aw=24."""
    return _ic(
        name="ic0",
        params={"SYS_ADDR_W": 24},
        targets=[
            _tgt("mem0", base=0x1000, size=0x100, phys_size=0x200,
                 sw_access="rw", region_type="memory", protocol="ahb"),
            _tgt("per0", base=0x40000000, size=0x1000,
                 sw_access="ro", region_type="periph", protocol="apb"),
        ],
        initiators=[_init("cpu", ["mem0", "per0"])],
    )


def _build_rm(ic, params=None):
    top = Module(name="t")
    top.interconnects = [ic]
    return SoCDiscoveryBackend(top).build_register_map(ic, params or {})


# ===========================================================================
# unit tier — pure encoders
# ===========================================================================

class TestEncoders:
    pytestmark = pytest.mark.unit

    def test_encode_name_le_pack(self):
        # DY-06: first 4 chars packed little-endian; short names null-padded.
        # "ab" -> 'a'(0x61) | 'b'(0x62)<<8 = 0x6261
        assert _encode_name("ab") == (0x61 | (0x62 << 8))
        # "abcdef" -> only first 4 chars 'abcd'.
        assert _encode_name("abcdef") == (
            ord("a") | (ord("b") << 8) | (ord("c") << 16) | (ord("d") << 24)
        )
        # empty -> 0 (all null pad).
        assert _encode_name("") == 0

    def test_encode_region_type(self):
        # DY-08: known case-insensitive -> enum; unknown / None -> 0.
        assert _encode_region_type("MEMORY") == 1
        assert _encode_region_type("memory") == 1
        assert _encode_region_type("periph") == 2
        assert _encode_region_type("peripheral") == 2
        assert _encode_region_type("debug") == 3
        assert _encode_region_type("weird") == 0
        assert _encode_region_type(None) == 0

    def test_encode_sw_access(self):
        assert _encode_sw_access("RW") == 3
        assert _encode_sw_access("ro") == 1
        assert _encode_sw_access("rwx") == 4
        assert _encode_sw_access("bogus") == 0
        assert _encode_sw_access(None) == 0

    def test_encode_protocol(self):
        assert _encode_protocol("AHB") == 0
        assert _encode_protocol("apb") == 1
        assert _encode_protocol("axi") == 0
        assert _encode_protocol(None) == 0

    def test_enum_map_contracts(self):
        # Pin the enum dicts the firmware decoder mirrors.
        assert _REGION_TYPE_MAP == {
            "memory": 1, "periph": 2, "peripheral": 2, "debug": 3,
        }
        assert _SW_ACCESS_MAP == {
            "none": 0, "ro": 1, "wo": 2, "rw": 3, "rwx": 4,
        }
        assert _PROTOCOL_MAP == {"ahb": 0, "apb": 1}


class TestVisibilityMask:
    pytestmark = pytest.mark.unit

    def test_bit_per_reachable_target(self):
        # DY-09: init reaching tgt0 and tgt2 of [t0,t1,t2] -> 0b101.
        init = _init("m", ["t0", "t2"])
        assert _build_visibility_mask(init, ["t0", "t1", "t2"]) == 0b101

    def test_unknown_target_ignored(self):
        # DY-10: a referenced target absent from target_names sets no bit.
        init = _init("m", ["ghost", "t1"])
        assert _build_visibility_mask(init, ["t0", "t1"]) == 0b010

    def test_empty_initiator_targets_zero(self):
        assert _build_visibility_mask(_init("m", []), ["t0", "t1"]) == 0


# ===========================================================================
# unit tier — register-map structure / packing
# ===========================================================================

class TestRegisterMap:
    pytestmark = pytest.mark.unit

    def test_header_magic_and_version(self):
        # DY-03: first register TABLE_ID reset == magic; TABLE_VERSION == 1.
        rm = _build_rm(_two_target_ic())
        regs = _regs_by_name(rm)
        assert rm.registers[0].name == "TABLE_ID"
        assert regs["TABLE_ID"].reset_value == _TABLE_MAGIC == 0x534F4344
        assert regs["TABLE_VERSION"].reset_value == _TABLE_VERSION == 1

    def test_table_size_packing(self):
        # DY-04: TABLE_SIZE = num_targets[7:0] | num_initiators[15:8] | aw[31:16]
        rm = _build_rm(_two_target_ic())
        regs = _regs_by_name(rm)
        assert regs["TABLE_SIZE"].reset_value == (2 | (1 << 8) | (24 << 16))

    def test_addr_width_from_param_else_default(self):
        # DY-05: addr_width pulled from SYS_ADDR_W; defaults to 32 when absent.
        rm24 = _build_rm(_two_target_ic())
        assert _regs_by_name(rm24)["TABLE_SIZE"].reset_value >> 16 == 24

        ic_def = _ic(targets=[_tgt("a", region_type="memory")],
                     initiators=[_init("m", ["a"])])
        rm_def = _build_rm(ic_def)
        assert _regs_by_name(rm_def)["TABLE_SIZE"].reset_value >> 16 == 32

    def test_target_attr_packing(self):
        # DY-07: TGT_ATTR = region[3:0] | sw_access[7:4] | protocol[15:8] | id[23:16]
        rm = _build_rm(_two_target_ic())
        regs = _regs_by_name(rm)
        # mem0: memory(1), rw(3), ahb(0), id 0
        assert regs["TGT_0_ATTR"].reset_value == (1 | (3 << 4) | (0 << 8) | (0 << 16))
        # per0: periph(2), ro(1), apb(1), id 1
        assert regs["TGT_1_ATTR"].reset_value == (2 | (1 << 4) | (1 << 8) | (1 << 16))

    def test_target_base_size_and_phys_size_expr(self):
        # phys_size as a parameter expression is resolved against params.
        ic = _ic(
            params={},
            targets=[_tgt("mem", base=0x2000, size=0x10,
                          phys_size="2 ** $AW", region_type="memory")],
            initiators=[_init("m", ["mem"])],
        )
        rm = _build_rm(ic, params={"AW": 10})
        regs = _regs_by_name(rm)
        assert regs["TGT_0_BASE"].reset_value == 0x2000
        # phys_size '2 ** 10' resolves to 1024.
        assert regs["TGT_0_SIZE"].reset_value == 1024

    def test_names_encoded(self):
        # INTERCONNECT_NAME / TGT_NAME / INIT_NAME carry packed-ASCII reset values.
        rm = _build_rm(_two_target_ic())
        regs = _regs_by_name(rm)
        assert regs["INTERCONNECT_NAME"].reset_value == _encode_name("ic0")
        assert regs["TGT_0_NAME"].reset_value == _encode_name("mem0")
        assert regs["INIT_0_NAME"].reset_value == _encode_name("cpu")

    def test_initiator_visibility_reg(self):
        # The encoded visibility mask lands in INIT_j_VISIBILITY.
        rm = _build_rm(_two_target_ic())
        regs = _regs_by_name(rm)
        # cpu sees mem0(bit0) + per0(bit1) -> 0b11.
        assert regs["INIT_0_VISIBILITY"].reset_value == 0b11

    def test_register_count_invariant(self):
        # DY-11: count == 4 header + 4*targets + 2*initiators.
        rm = _build_rm(_two_target_ic())  # 2 targets, 1 initiator
        assert len(rm.registers) == 4 + 4 * 2 + 2 * 1 == 14

        ic3 = _ic(
            params={"SYS_ADDR_W": 32},
            targets=[_tgt(f"t{i}", base=i << 16, region_type="memory") for i in range(3)],
            initiators=[_init("a", ["t0"]), _init("b", ["t1", "t2"])],
        )
        rm3 = _build_rm(ic3)
        assert len(rm3.registers) == 4 + 4 * 3 + 2 * 2 == 20

    def test_register_map_identity_fields(self):
        # DY-12: name / module / address_width / data_width / gen contract.
        rm = _build_rm(_two_target_ic())
        assert rm.name == "ic0_discovery"
        assert rm.module == "ic0_discovery_table"
        assert rm.address_width == 12
        assert rm.data_width == 32
        assert rm.gen is True

    def test_header_fields_present_id_field_absent(self):
        # DY-13 (field presence half): TABLE_VERSION / TABLE_SIZE carry fields;
        # the bare TABLE_ID has none.
        rm = _build_rm(_two_target_ic())
        regs = _regs_by_name(rm)
        assert regs["TABLE_ID"].fields == []
        assert [f.name for f in regs["TABLE_VERSION"].fields] == ["VERSION", "RESERVED"]
        assert [f.name for f in regs["TABLE_SIZE"].fields] == [
            "NUM_TARGETS", "NUM_INITIATORS", "ADDR_WIDTH",
        ]


# ===========================================================================
# unit tier — generate(): gen-filter, recursion, files
# ===========================================================================

class TestGenerate:
    pytestmark = pytest.mark.unit

    def test_only_gen_true_interconnects(self, tmp_path):
        # DY-01: a gen:False interconnect produces no table.
        top = Module(name="filt")
        top.interconnects = [
            _ic("yes", gen=True,
                targets=[_tgt("a", region_type="memory")],
                initiators=[_init("m", ["a"])]),
            _ic("no", gen=False,
                targets=[_tgt("b", region_type="memory")],
                initiators=[_init("m", ["b"])]),
        ]
        results = SoCDiscoveryBackend(top).generate(tmp_path)
        names = [name for (name, _rm, _path) in results]
        assert names == ["yes"]
        assert (tmp_path / "discovery" / "yes_discovery.yaml").exists()
        assert not (tmp_path / "discovery" / "no_discovery.yaml").exists()

    def test_recurses_into_child_modules(self, tmp_path):
        # DY-02: discovery recurses into resolved child instances' interconnects.
        child = Module(name="child_ss")
        child.interconnects = [
            _ic("child_ic", gen=True,
                targets=[_tgt("cmem", region_type="memory")],
                initiators=[_init("cm", ["cmem"])]),
        ]
        top = Module(name="par")
        top.interconnects = [
            _ic("top_ic", gen=True,
                targets=[_tgt("tmem", region_type="memory")],
                initiators=[_init("tm", ["tmem"])]),
        ]
        top.instances = [
            Instance(instance_name="u_child", module_name="child_ss",
                     resolved_module=child),
        ]
        results = SoCDiscoveryBackend(top).generate(tmp_path)
        names = sorted(name for (name, _rm, _p) in results)
        assert names == ["child_ic", "top_ic"]

    def test_generate_returns_tuples_and_writes_yaml(self, tmp_path):
        top = Module(name="t")
        ic = _two_target_ic()
        top.interconnects = [ic]
        results = SoCDiscoveryBackend(top).generate(tmp_path)
        assert len(results) == 1
        name, rm, path = results[0]
        assert name == "ic0"
        assert rm.name == "ic0_discovery"
        assert path == tmp_path / "discovery" / "ic0_discovery.yaml"
        assert path.exists()


# ===========================================================================
# unit tier — emitted YAML structure (device-model-consumed contract)
# ===========================================================================

class TestYamlStructure:
    pytestmark = pytest.mark.unit

    def _emit_and_load(self, tmp_path):
        top = Module(name="t")
        top.interconnects = [_two_target_ic()]
        (_name, _rm, path) = SoCDiscoveryBackend(top).generate(tmp_path)[0]
        raw = path.read_text()
        return raw, yaml.safe_load(raw)

    def test_top_level_register_map_keys(self, tmp_path):
        # DY-14 (structure): the consumed top-level shape.
        _raw, doc = self._emit_and_load(tmp_path)
        assert set(doc) == {"register_map"}
        rm = doc["register_map"]
        for key in ("name", "module", "gen", "description",
                    "address_width", "data_width", "registers"):
            assert key in rm, f"missing register_map key {key!r}"
        assert rm["name"] == "ic0_discovery"
        assert rm["module"] == "ic0_discovery_table"
        assert rm["gen"] is True
        assert rm["address_width"] == 12
        assert rm["data_width"] == 32
        assert isinstance(rm["registers"], list) and rm["registers"]

    def test_registers_encode_initiators_targets_addresses(self, tmp_path):
        # The reader recovers initiators / targets / addresses from the encoded
        # register reset values — assert that contract holds in the emitted YAML.
        _raw, doc = self._emit_and_load(tmp_path)
        regs = {r["name"]: r for r in doc["register_map"]["registers"]}

        # Magic + sizes.
        assert int(regs["TABLE_ID"]["reset_value"], 16) == _TABLE_MAGIC
        size = int(regs["TABLE_SIZE"]["reset_value"], 16)
        assert (size & 0xFF) == 2                 # num targets
        assert ((size >> 8) & 0xFF) == 1          # num initiators
        assert ((size >> 16) & 0xFFFF) == 24      # address width

        # Target base addresses recovered from TGT_i_BASE.
        assert int(regs["TGT_0_BASE"]["reset_value"], 16) == 0x1000
        assert int(regs["TGT_1_BASE"]["reset_value"], 16) == 0x40000000
        # Initiator visibility recovered from INIT_0_VISIBILITY.
        assert int(regs["INIT_0_VISIBILITY"]["reset_value"], 16) == 0b11

    def test_offset_and_reset_value_formats(self, tmp_path):
        # DY-13 (formats): offset 0x%03X, reset_value 0x%08X.
        raw, doc = self._emit_and_load(tmp_path)
        regs = doc["register_map"]["registers"]
        # offsets like '0x000', '0x004' ... (3 hex digits, upper-case).
        assert regs[0]["offset"] == "0x000"
        assert regs[1]["offset"] == "0x004"
        # reset_value is an 8-hex-digit string (kept quoted in raw YAML).
        rv = regs[0]["reset_value"]
        assert rv == "0x534F4344"
        assert len(rv) == len("0x") + 8
        # raw text keeps the offset quoted (string, not int).
        assert "offset: '0x000'" in raw

    def test_fields_emitted_only_when_present(self, tmp_path):
        # DY-13 (fields key): TABLE_ID has no 'fields'; TABLE_SIZE does.
        _raw, doc = self._emit_and_load(tmp_path)
        regs = {r["name"]: r for r in doc["register_map"]["registers"]}
        assert "fields" not in regs["TABLE_ID"]
        assert "fields" in regs["TABLE_SIZE"]
        field_names = [f["name"] for f in regs["TABLE_SIZE"]["fields"]]
        assert field_names == ["NUM_TARGETS", "NUM_INITIATORS", "ADDR_WIDTH"]


# ===========================================================================
# golden tier
# ===========================================================================

class TestGolden:
    pytestmark = pytest.mark.golden

    @pytest.mark.parametrize(
        "fixture, ic_name, num_targets, num_initiators",
        [
            ("minimal_soc", "minimal_ahb_interconnect", 1, 1),
            ("two_region_soc", "two_region_ahb_interconnect", 2, 1),
            ("apb_bridge_soc", "apb_bridge_ahb_interconnect", 2, 1),
        ],
    )
    def test_discovery_yaml_golden(self, fixture, ic_name, num_targets,
                                   num_initiators, fixtures_dir, tmp_path, request):
        # DY-14: full .yaml byte-match (# Generated: stripped by the helper) with
        # structural teeth on the consumed contract.
        top = build_module_from_yaml(fixtures_dir, f"{fixture}.yaml")
        results = SoCDiscoveryBackend(top).generate(tmp_path)
        assert len(results) == 1
        name, rm, path = results[0]
        assert name == ic_name
        text = path.read_text()

        # Structural teeth — a vacuous / errored golden cannot pass.
        doc = yaml.safe_load(text)
        assert "register_map" in doc
        rmd = doc["register_map"]
        assert rmd["name"] == f"{ic_name}_discovery"
        assert rmd["module"] == f"{ic_name}_discovery_table"
        assert rmd["address_width"] == 12
        assert rmd["data_width"] == 32
        regs = rmd["registers"]
        assert len(regs) == 4 + 4 * num_targets + 2 * num_initiators
        assert regs[0]["name"] == "TABLE_ID"
        assert int(regs[0]["reset_value"], 16) == _TABLE_MAGIC

        assert_matches_snapshot(
            text, SNAPSHOTS / fixture / f"{ic_name}_discovery.yaml", request,
        )
