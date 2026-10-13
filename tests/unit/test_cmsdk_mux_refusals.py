# Copyright 2026, SoC Labs (www.soclabs.org)
"""``impl: cmsdk_mux`` refuses, loudly, every configuration it used to render
wrongly while exiting 0.

Each case is the cmsdk_mux fixture (``tests/fixtures/cmsdk_mux_soc.yaml``)
with one change, built here through the real parser and builder. A refusal
must show in three places: ``plan_cmsdk_mux`` raises a ``CmsdkMuxError``
naming the interconnect, the offending item and what to do; the validator
reports it as an ``interconnect`` error; and the AHB backend raises without
leaving a ``<ic>_lite.v``. The CLI tests at the end check that
``python -m soc_model`` stops non-zero BEFORE generating anything, and that a
core left by an earlier good run is deleted.

Case ids (c09, c25, ...) follow the independent review that found them.
"""

import dataclasses
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from soc_model.backends import cmsdk_mux
from soc_model.backends.ahb import SoCAhbBackend
from soc_model.backends.cmsdk_mux import CmsdkMuxError, plan_cmsdk_mux
from soc_model.model import Interconnect, InterconnectInitiator
from soc_model.validator import SoCValidator

from tests.helpers import build_module_from_yaml, arm_free_env

pytestmark = pytest.mark.unit

FIXTURE = "cmsdk_mux_soc.yaml"
IC = "cmsdk_mux_ahb_interconnect"
PKG_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Building a case from the fixture's YAML
# ---------------------------------------------------------------------------
def _base(fixtures_dir):
    return yaml.safe_load((fixtures_dir / FIXTURE).read_text())


def _ic(d):
    return d["module"]["interconnects"][0]


def _tgt(d, name):
    return next(t for t in _ic(d)["targets"] if t["name"] == name)


def _ini(d, name):
    return next(i for i in _ic(d)["initiators"] if i["name"] == name)


def _add_region(d, name, base, size, reach_from="ext_s"):
    """A tiny_region instance on the interconnect, reached by one initiator."""
    d["module"]["instances"].append({
        "instance_name": f"u_{name}", "module": "tiny_region", "addressable": True,
        "params": {"SYS_ADDR_W": "$SYS_ADDR_W", "SYS_DATA_W": "$SYS_DATA_W",
                   "RAM_ADDR_W": "$SRAM_RAM_ADDR_W"},
        "connections": [{"port": "HCLK", "conn": "sys_hclk"},
                        {"port": "HRESETn", "conn": "sys_hresetn"},
                        {"port": "ahb_slave", "conn": f"{IC}.{name}"}]})
    _ic(d)["targets"].append({"name": name, "instance": f"u_{name}", "base": base,
                              "size": size, "sw_access": "rw",
                              "region_type": "memory", "desc": name})
    _ini(d, reach_from)["targets"].append({"name": name})


def _add_passthrough_target(d, name, base, size, iface, reach_from="cpu_m"):
    d["module"]["interfaces"].append({
        "name": iface, "type": "ahb", "direction": "initiator",
        "params": {"ADDR_WIDTH": "$SYS_ADDR_W", "DATA_WIDTH": "$SYS_DATA_W"},
        "desc": iface})
    _ic(d)["targets"].append({"name": name, "base": base, "size": size,
                              "sw_access": "rw", "passthrough": iface, "desc": name})
    _ini(d, reach_from)["targets"].append({"name": name})


def _small_system(d):
    """Shrink the CPU's passthrough window to 0x2000_0000-0x2FFF_FFFF, leaving
    room above it for extra targets."""
    _tgt(d, "system")["size"] = 0x10000000


def _write(d, where):
    where.mkdir(parents=True, exist_ok=True)
    path = where / "case.yaml"
    path.write_text(yaml.safe_dump(d, sort_keys=False))
    return path


def _build(d, tmp_path, fixtures_dir):
    path = _write(d, tmp_path / "yaml")
    return build_module_from_yaml(path.parent, path.name, lib_dirs=[fixtures_dir])


def _case(fixtures_dir, tmp_path, *edits):
    d = _base(fixtures_dir)
    for edit in edits:
        edit(d)
    return _build(d, tmp_path, fixtures_dir)


def _ic_of(top):
    return top.interconnects[0]


def _ic_errors(top):
    return [m for m in SoCValidator(top).validate_all()
            if m.level == "error" and m.category == "interconnect"]


def _assert_backend_refuses(top, tmp_path, pattern):
    build = tmp_path / "build"
    with pytest.raises(CmsdkMuxError, match=pattern):
        SoCAhbBackend(top).generate_all(build)
    assert not list(build.rglob("*_lite.v")), "a refused interconnect left a core"


def _assert_refused(top, tmp_path, pattern):
    """plan, validator and backend all refuse, with ``pattern`` in the message."""
    with pytest.raises(CmsdkMuxError) as exc:
        plan_cmsdk_mux(_ic_of(top))
    msg = str(exc.value)
    assert msg.startswith(f"{IC}: "), msg
    assert re.search(pattern, msg), msg
    errs = _ic_errors(top)
    assert any(re.search(pattern, m.message) for m in errs), [str(m) for m in errs]
    _assert_backend_refuses(top, tmp_path, pattern)
    return msg


def _rename_target(top, old, new):
    ic = _ic_of(top)
    for t in ic.targets:
        if t.name == old:
            t.name = new
    for init in ic.initiators:
        for it in init.targets:
            if it.name == old:
                it.name = new


# ---------------------------------------------------------------------------
# (a) regions outside the 32-bit address space
# ---------------------------------------------------------------------------
def test_c09_region_running_past_4g_is_refused(fixtures_dir, tmp_path):
    """system 0x2000_0000 + 0xF000_0000 runs to 0x1_0FFF_FFFF. The core used
    to drop the 0x1_0000_0000 bound and decode 'system' as >= 0x2000_0000."""
    def edit(d):
        _tgt(d, "system")["size"] = 0xF0000000
    top = _case(fixtures_dir, tmp_path, edit)
    _assert_refused(top, tmp_path,
                    r"initiator 'cpu_m' \(remap\[0\]=0\) sees 'system' at "
                    r"0x20000000-0x10fffffff, outside the 32-bit address space.*"
                    r"Keep every window inside it")


def test_c10_base_above_4g_is_refused(fixtures_dir, tmp_path):
    """dat_0 at 0x1_0000_0000 used to be truncated onto 0x0 (over rom_0)."""
    def edit(d):
        _tgt(d, "dat_0")["base"] = 0x100000000
    top = _case(fixtures_dir, tmp_path, edit)
    _assert_refused(top, tmp_path, r"sees 'dat_0' at 0x100000000-0x107ffffff, outside "
                                   r"the 32-bit address space")


def test_c12_region_ending_at_top_of_space_is_accepted(fixtures_dir, tmp_path):
    """Control: a region ending exactly at 0xFFFF_FFFF is inside the space."""
    def edit(d):
        _tgt(d, "system")["size"] = 0xD0000000
        _add_region(d, "top", 0xF0000000, 0x10000000)
    plan = plan_cmsdk_mux(_ic_of(_case(fixtures_dir, tmp_path, edit)))
    assert "b_dec_a >= 5'h1e" in plan["shared_exprs"]["TOP"]


@pytest.mark.parametrize("lo, hi, lsb, width", [
    (0x0, 0x100000000, 0, 32),                # past the top
    (0x100000000, 0x107FFFFFF, 27, 5),        # above 4 GB
    (-0x1000, 0xFFF, 12, 20),                 # below 0
    (0x2000, 0x1FFF, 12, 20),                 # empty
    (0x18000400, 0x180007FF, 12, 20),         # off the HADDR[31:12] grid
    (0x0, 0xFFF, 12, 21),                     # width does not match lsb
], ids=["past_top", "above_4g", "negative", "empty", "off_grid", "width"])
def test_range_expr_refuses_what_it_cannot_decode_exactly(lo, hi, lsb, width):
    """Defence in depth: the expression builder itself never truncates."""
    with pytest.raises(CmsdkMuxError, match=r"refusing to truncate"):
        cmsdk_mux._range_expr("a", lo, hi, lsb, width)


def test_range_expr_still_decodes_the_top_region():
    assert cmsdk_mux._range_expr("a", 0xF0000000, 0xFFFFFFFF, 28, 4) == "(a == 4'hf)"
    assert cmsdk_mux._range_expr("a", 0x20000000, 0xFFFFFFFF, 29, 3) == "(a >= 3'h1)"


# ---------------------------------------------------------------------------
# (b) private passthrough targets beyond the front-end slave mux
# ---------------------------------------------------------------------------
def _private_targets(n):
    def edit(d):
        _small_system(d)
        for k in range(1, n):
            _add_passthrough_target(d, f"sys{k}", 0x20000000 + k * 0x10000000,
                                    0x10000000, f"sys_m{k}")
    return edit


def test_c25_ten_private_targets_are_refused(fixtures_dir, tmp_path):
    """The front-end slave mux wires PORT1..PORT9; a 10th private target was
    left unwired (its reads returned 0 with no wait and OKAY)."""
    top = _case(fixtures_dir, tmp_path, _private_targets(10))
    _assert_refused(top, tmp_path,
                    r"initiator 'cpu_m' reaches 10 passthrough targets on its private "
                    r"path .*at most 9\. Merge passthrough windows or use impl: busmatrix")


def test_nine_private_targets_are_accepted(fixtures_dir, tmp_path):
    plan = plan_cmsdk_mux(_ic_of(_case(fixtures_dir, tmp_path, _private_targets(9))))
    cpu = next(i for i in plan["initiators"] if i["name"] == "cpu_m")
    assert len(cpu["private"]) == 9


# ---------------------------------------------------------------------------
# (c) remap specs
# ---------------------------------------------------------------------------
def _remap_spec(d, init):
    return _ini(d, init)["targets"][1]["visibility"][0]["remap"]


@pytest.mark.parametrize("bit", [4, -1, "0", True, None, 1.0],
                         ids=["c01_4", "c02_neg", "c36_str", "bool", "null", "float"])
def test_remap_bit_outside_0_to_3_is_refused(fixtures_dir, tmp_path, bit):
    """REMAP is [3:0]: remap_bit 4 and -1 selected no REMAP bit that exists,
    and '0' / True rendered only by accident of str()."""
    def edit(d):
        for init in ("cpu_m", "ext_s"):
            _remap_spec(d, init)["remap_bit"] = bit
    top = _case(fixtures_dir, tmp_path, edit)
    _assert_refused(top, tmp_path,
                    r"initiator 'cpu_m', target 'ram_0', visibility\[0\] has remap_bit "
                    r".*must be an integer 0\.\.3\. Fix remap_bit")


def test_remap_window_without_remap_bit_is_refused(fixtures_dir, tmp_path):
    def edit(d):
        for init in ("cpu_m", "ext_s"):
            del _remap_spec(d, init)["remap_bit"]
    top = _case(fixtures_dir, tmp_path, edit)
    _assert_refused(top, tmp_path, r"visibility\[0\] is a remap window with no "
                                   r"remap_bit; set remap_bit: 0\.\.3")


@pytest.mark.parametrize("where", ["remap_spec", "window"])
def test_unknown_remap_behaviour_is_refused(fixtures_dir, tmp_path, where):
    """A misspelt 'move' used to act as 'alias': the window it should have
    moved stayed visible."""
    def edit(d):
        for init in ("cpu_m", "ext_s"):
            if where == "remap_spec":
                _remap_spec(d, init)["remap_behaviour"] = "mvoe"
            else:
                _ini(d, init)["targets"][1]["visibility"][1]["remap_behaviour"] = "mvoe"
    top = _case(fixtures_dir, tmp_path, edit)
    _assert_refused(top, tmp_path, r"has remap_behaviour 'mvoe'; expected one of "
                                   r"\['alias', 'move', 'none'\]")


def test_remap_that_is_not_a_mapping_is_refused(fixtures_dir, tmp_path):
    """``remap: 1`` used to make the window permanently visible."""
    def edit(d):
        _small_system(d)
        _ini(d, "cpu_m")["targets"][2] = {"name": "dat_0", "visibility": [
            {"base": 0x18000000}, {"base": 0x40000000, "remap": 1}]}
    top = _case(fixtures_dir, tmp_path, edit)
    _assert_refused(top, tmp_path, r"target 'dat_0', visibility\[1\] has remap: 1; "
                                   r"it must be a mapping")


def test_c38_second_remap_bit_is_accepted(fixtures_dir, tmp_path):
    """Control: bits 0..3 are fine (c38: dat_0 over the rom alias on bit 1)."""
    def edit(d):
        for init in ("cpu_m", "ext_s"):
            _ini(d, init)["targets"][2] = {"name": "dat_0", "visibility": [
                {"base": 0x08000000, "remap": {"remapping": True,
                                               "remap_behaviour": "alias",
                                               "remap_bit": 1}},
                {"base": 0x18000000}]}
    assert plan_cmsdk_mux(_ic_of(_case(fixtures_dir, tmp_path, edit)))["remap_bits"] == [0, 1]


# ---------------------------------------------------------------------------
# (d) impl: present but unusable, and a misspelt impl key
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("impl, got", [
    ("", "empty"), ("  ", "empty"), (None, "null"), (5, r"not a string \(5\)"),
    (["cmsdk_mux"], r"not a string \(\['cmsdk_mux'\]\)"),
], ids=["c17_empty", "blank", "c18_null", "int", "list"])
def test_impl_present_but_unusable_is_refused(fixtures_dir, tmp_path, impl, got):
    """c17: ``impl: ""`` used to build a bus matrix without a word."""
    def edit(d):
        _ic(d)["impl"] = impl
    top = _case(fixtures_dir, tmp_path, edit)
    pattern = (rf"'impl:' is present but {got}; write impl: busmatrix or impl: "
               rf"cmsdk_mux, or delete the key")
    errs = _ic_errors(top)
    assert len(errs) == 1 and re.search(pattern, errs[0].message), [str(m) for m in errs]
    assert errs[0].message.startswith(f"{IC}: ")
    _assert_backend_refuses(top, tmp_path, pattern)


@pytest.mark.parametrize("key, value", [
    ("imp", "cmsdk_mux"), ("Impl", "cmsdk_mux"), ("implementation", "cmsdk_mux"),
    ("ipml", "cmsdk_mux"), ("backend", "cmsdk_mux"),
    ("interconnect_impl", "cmsdk-mux"),
], ids=["c19_imp", "case", "long", "swap", "value_only", "dash_value"])
def test_misspelt_impl_key_is_refused(fixtures_dir, tmp_path, key, value):
    """c19: ``imp: cmsdk_mux`` was ignored and a bus matrix was built."""
    def edit(d):
        del _ic(d)["impl"]
        _ic(d)[key] = value
    top = _case(fixtures_dir, tmp_path, edit)
    assert cmsdk_mux.ic_impl(_ic_of(top)) == "busmatrix"
    pattern = (rf"unknown interconnect key '{key}' \(value '{value}'\) is ignored, so "
               rf"this interconnect would be built as impl: busmatrix\. Did you mean "
               rf"'impl'\?")
    errs = _ic_errors(top)
    assert len(errs) == 1 and re.search(pattern, errs[0].message), [str(m) for m in errs]
    _assert_backend_refuses(top, tmp_path, pattern)


def test_unknown_key_on_cmsdk_mux_is_a_warning(fixtures_dir, tmp_path):
    def edit(d):
        _ic(d)["colour"] = "blue"
    top = _case(fixtures_dir, tmp_path, edit)
    msgs = [m for m in SoCValidator(top).validate_all() if m.category == "interconnect"]
    assert [(m.level, m.message) for m in msgs] == [
        ("warning", f"{IC}: unknown interconnect key 'colour' is ignored")]
    plan_cmsdk_mux(_ic_of(top))


def test_unknown_key_on_busmatrix_adds_no_validator_message(fixtures_dir, tmp_path):
    """Bus-matrix behaviour is unchanged: the key is recorded (for reporting)
    but the validator messages, which the HTML/JSON reports embed, are not."""
    plain = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
    d = yaml.safe_load((fixtures_dir / "minimal_soc.yaml").read_text())
    _ic(d)["colour"] = "blue"
    path = _write(d, tmp_path / "yaml")
    top = build_module_from_yaml(path.parent, path.name, lib_dirs=[fixtures_dir])
    assert cmsdk_mux.unknown_interconnect_keys(_ic_of(top)) == {"colour": "blue"}
    assert ([str(m) for m in SoCValidator(top).validate_all()]
            == [str(m) for m in SoCValidator(plain).validate_all()])


def test_unknown_keys_are_not_a_model_field(fixtures_dir):
    """Recorded as a plain attribute: no dataclass field, so model dumps,
    reports and the schema doc do not change."""
    assert "_unknown_yaml_keys" not in {f.name for f in dataclasses.fields(Interconnect)}
    top = build_module_from_yaml(fixtures_dir, FIXTURE)
    assert cmsdk_mux.unknown_interconnect_keys(_ic_of(top)) == {}


# ---------------------------------------------------------------------------
# (e) non-AHB interconnect types
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ic_type", ["axi4", "apb", "AHB_LITE"],
                         ids=["c20_axi4", "c21_apb", "upper"])
def test_non_ahb_type_is_refused(fixtures_dir, tmp_path, ic_type):
    """c20/c21: an AXI or APB interconnect was rendered as an AHB core."""
    def edit(d):
        _ic(d)["type"] = ic_type
    top = _case(fixtures_dir, tmp_path, edit)
    _assert_refused(top, tmp_path,
                    rf"type: '{ic_type}', but impl: cmsdk_mux builds an AHB-Lite "
                    rf"interconnect only.*Use type: ahb_lite")


def test_type_ahb_is_accepted(fixtures_dir, tmp_path):
    def edit(d):
        _ic(d)["type"] = "ahb"
    plan_cmsdk_mux(_ic_of(_case(fixtures_dir, tmp_path, edit)))


# ---------------------------------------------------------------------------
# (f) sizes and apertures
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("size", [0, -0x1000], ids=["c11_zero", "negative"])
def test_size_not_positive_is_refused(fixtures_dir, tmp_path, size):
    """c11: a zero-size dat_0 generated a core in which dat_0 is never selected."""
    def edit(d):
        _tgt(d, "dat_0")["size"] = size
    top = _case(fixtures_dir, tmp_path, edit)
    _assert_refused(top, tmp_path, rf"target 'dat_0' has size {size}; a region needs a "
                                   rf"size > 0.*Set size:")


def test_aperture_not_positive_is_refused(fixtures_dir, tmp_path):
    def edit(d):
        _ini(d, "ext_s")["targets"][2] = {"name": "dat_0", "visibility": [
            {"base": 0x18000000, "aperture": 0}]}
    top = _case(fixtures_dir, tmp_path, edit)
    _assert_refused(top, tmp_path, r"initiator 'ext_s', target 'dat_0', visibility\[0\] "
                                   r"has aperture 0; a window needs an aperture > 0")


def test_non_numeric_size_is_refused(fixtures_dir, tmp_path):
    """Used to escape as a bare ValueError from the address-map code. (Only
    plan and backend here: the validator's canonical overlap check, shared
    with the bus matrix, does arithmetic on the raw size first.)"""
    def edit(d):
        _tgt(d, "dat_0")["size"] = "big"
    top = _case(fixtures_dir, tmp_path, edit)
    pattern = r"target 'dat_0' size is 'big'; impl: cmsdk_mux needs a number here"
    with pytest.raises(CmsdkMuxError, match=pattern):
        plan_cmsdk_mux(_ic_of(top))
    _assert_backend_refuses(top, tmp_path, pattern)


# ---------------------------------------------------------------------------
# (g) names that collide in the generated core
# ---------------------------------------------------------------------------
def test_c26_target_named_default_is_refused(fixtures_dir, tmp_path):
    """c26: redeclared b_dec_DEFAULT (iverilog error; verilator UNOPTFLAT)."""
    def edit(d):
        _small_system(d)
        _add_region(d, "default", 0x30000000, 0x1000)
    top = _case(fixtures_dir, tmp_path, edit)
    _assert_refused(top, tmp_path, r"target name 'default' is reserved by impl: "
                                   r"cmsdk_mux: .*b_dec_DEFAULT.*Rename the target")


def test_c27_names_equal_after_upper_casing_are_refused(fixtures_dir, tmp_path):
    """c27: dat_0 and DAT_0 both became HADDR_DAT_0 (28 verilator errors)."""
    def edit(d):
        _small_system(d)
        _add_region(d, "DAT_0", 0x30000000, 0x1000)
    top = _case(fixtures_dir, tmp_path, edit)
    _assert_refused(top, tmp_path, r"target 'dat_0' and target 'DAT_0' both become "
                                   r"'DAT_0' in the generated core.*differ other than "
                                   r"by case")


def test_c28_initiator_and_target_with_one_name_are_refused(fixtures_dir, tmp_path):
    """c28: HADDR_EXT_S was both an input and an output of the core."""
    def edit(d):
        _small_system(d)
        _add_region(d, "ext_s", 0x30000000, 0x1000, reach_from="cpu_m")
    top = _case(fixtures_dir, tmp_path, edit)
    _assert_refused(top, tmp_path, r"initiator 'ext_s' and target 'ext_s' both become "
                                   r"'EXT_S'")


def test_duplicate_initiator_names_are_refused(fixtures_dir, tmp_path):
    top = _case(fixtures_dir, tmp_path)
    _ic_of(top).initiators.append(InterconnectInitiator(name="CPU_M"))
    _assert_refused(top, tmp_path, r"initiator 'cpu_m' and initiator 'CPU_M' both "
                                   r"become 'CPU_M'")


@pytest.mark.parametrize("name", ["dat-0", "0dat", "d at", "dät", ""],
                         ids=["dash", "digit", "space", "non_ascii", "empty"])
def test_name_that_is_not_a_verilog_identifier_is_refused(fixtures_dir, tmp_path, name):
    top = _case(fixtures_dir, tmp_path)
    _rename_target(top, "dat_0", name)
    _assert_refused(top, tmp_path, rf"target name {re.escape(repr(name))} is not a plain "
                                   rf"Verilog identifier.*Rename the target")


@pytest.mark.parametrize("name", ["a", "A", "shared", "Shared"])
def test_names_differing_only_in_case_from_core_wires_are_refused(fixtures_dir, tmp_path, name):
    """b_dec_A vs b_dec_a, fe_<I>_sel_SHARED vs fe_<I>_sel_shared: legal in
    case-sensitive Verilog, merged by a case-insensitive tool."""
    top = _case(fixtures_dir, tmp_path)
    _rename_target(top, "dat_0", name)
    _assert_refused(top, tmp_path, rf"target name '{name}' is reserved by impl: "
                                   rf"cmsdk_mux: .*differ only in case")


def test_interconnect_name_that_is_not_an_identifier_is_refused(fixtures_dir):
    top = build_module_from_yaml(fixtures_dir, FIXTURE)
    _ic_of(top).name = "my-ic"
    with pytest.raises(CmsdkMuxError, match=r"'my-ic': .*must be a plain Verilog "
                                            r"identifier.*Rename the interconnect"):
        plan_cmsdk_mux(_ic_of(top))


# ---------------------------------------------------------------------------
# (h) no stale core after a refusal
# ---------------------------------------------------------------------------
def test_refusal_removes_the_core_an_earlier_run_left(fixtures_dir, tmp_path, capsys):
    top = build_module_from_yaml(fixtures_dir, FIXTURE)
    build = tmp_path / "build"
    SoCAhbBackend(top).generate_all(build)
    lite = build / "rtl" / IC / f"{IC}_lite.v"
    flist = build / "flist" / "cmsdk_mux_interconnect.flist"
    assert lite.is_file() and flist.is_file()
    capsys.readouterr()

    # b4: dat_0 moved over ram_0, a refusal 0950612 already made (after
    # writing nothing new, but leaving the old core in place)
    next(t for t in _ic_of(top).targets if t.name == "dat_0").base = 0x14000000
    with pytest.raises(CmsdkMuxError, match=r"overlapping effective regions"):
        SoCAhbBackend(top).generate_all(build)
    assert not lite.exists(), "the stale core survived a refusal"
    assert not flist.exists(), "the stale cmsdk_mux flist survived a refusal"
    out = capsys.readouterr().out
    assert f"Removed stale {lite}" in out and f"Removed stale {flist}" in out


def test_stale_cleanup_never_touches_bus_matrix_outputs(tmp_path):
    """The bus-matrix core lives one level deeper and its flist comes from
    another template: neither is ever listed for deletion."""
    bm_core = tmp_path / "rtl" / IC / IC / f"{IC}_lite.v"
    bm_core.parent.mkdir(parents=True)
    bm_core.write_text("// bus matrix core\n")
    flist = tmp_path / "flist" / "cmsdk_mux_interconnect.flist"
    flist.parent.mkdir(parents=True)
    flist.write_text("// Template:  ahb_interconnect.flist.j2\n")
    assert cmsdk_mux.remove_stale_outputs(IC, tmp_path) == []
    assert bm_core.exists() and flist.exists()
    assert cmsdk_mux.remove_stale_outputs("../x", tmp_path) == []


# ---------------------------------------------------------------------------
# (i) the CLI stops before generating anything
# ---------------------------------------------------------------------------
def _cli(yaml_path, build_dir, fixtures_dir, *extra):
    env = arm_free_env()
    env.update(PYTHONPATH=str(PKG_ROOT), PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [sys.executable, "-m", "soc_model", str(yaml_path),
         "--build-dir", str(build_dir), "--lib-dir", str(fixtures_dir), *extra],
        cwd=str(PKG_ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, timeout=300)


def _c09(d):
    _tgt(d, "system")["size"] = 0xF0000000


def _c17(d):
    _ic(d)["impl"] = ""


def _c19(d):
    del _ic(d)["impl"]
    _ic(d)["imp"] = "cmsdk_mux"


def _c26(d):
    _small_system(d)
    _add_region(d, "default", 0x30000000, 0x1000)


def _c14_canonical_overlap(d):
    """What c14's single validator error was: the targets' canonical windows
    overlap ('system' 0x2000_0000-0xFFFF_FFFF over r0..r5 at 0x3000_0000), an
    'address' error on a cmsdk_mux interconnect, although each initiator's
    own map is unambiguous. The CLI used to report it and generate anyway."""
    for k in range(6):
        _add_region(d, f"r{k}", 0x30000000 + k * 0x1000, 0x1000)


@pytest.mark.parametrize("edit, why", [
    (_c09, r"sees 'system' at 0x20000000-0x10fffffff"),
    (_c17, r"'impl:' is present but empty"),
    (_c19, r"unknown interconnect key 'imp'"),
    (_c26, r"target name 'default' is reserved"),
    (_c14_canonical_overlap, r"\(address\) .*Address overlap: 'system'"),
], ids=["c09", "c17", "c19", "c26", "c14"])
def test_cli_refuses_before_generating(fixtures_dir, tmp_path, edit, why):
    d = _base(fixtures_dir)
    edit(d)
    build = tmp_path / "build"
    proc = _cli(_write(d, tmp_path / "yaml"), build, fixtures_dir)
    assert proc.returncode != 0, proc.stdout[-2000:]
    assert "ERROR: refusing to generate" in proc.stderr, proc.stderr
    assert re.search(why, proc.stderr), proc.stderr
    assert "Nothing was generated." in proc.stderr
    written = [p for p in build.rglob("*") if p.is_file()] if build.exists() else []
    assert written == []


def test_cli_refusal_deletes_the_core_a_good_run_left(fixtures_dir, tmp_path):
    """stale1/stale2: a good run, then a refused run into the same build dir."""
    build = tmp_path / "build"
    good = _cli(fixtures_dir / FIXTURE, build, fixtures_dir)
    assert good.returncode == 0, good.stderr[-2000:]
    lite = build / "rtl" / IC / f"{IC}_lite.v"
    assert lite.is_file()

    d = _base(fixtures_dir)
    _tgt(d, "dat_0")["base"] = 0x14000000       # b4: overlaps ram_0, refused
    bad = _cli(_write(d, tmp_path / "yaml"), build, fixtures_dir)
    assert bad.returncode != 0
    assert not lite.exists(), "the core from the good run survived the refusal"
    assert f"Removed stale {lite}" in bad.stderr, bad.stderr


def test_cli_bus_matrix_validator_errors_stop_unless_waived(fixtures_dir, tmp_path):
    """A validator error that concerns only a bus-matrix interconnect used to be
    printed while generation carried on. Since Train 7 (G8) every validation
    error stops the render, unless --validation-waive names it with a reason."""
    d = yaml.safe_load((fixtures_dir / "minimal_soc.yaml").read_text())
    _ic(d)["initiators"][0]["targets"].append({"name": "nosuch"})
    build = tmp_path / "build"
    y = _write(d, tmp_path / "yaml")
    proc = _cli(y, build, fixtures_dir)
    assert "which is not defined in targets" in proc.stdout
    assert proc.returncode == 1 and "refusing to generate: 1 validation error(s)" in proc.stderr
    assert not (build / "rtl").exists()
    proc = _cli(y, build, fixtures_dir, "--validation-waive",
                "reference:minimal_ahb_interconnect.cpu_m=bus-matrix keeps the old behaviour here")
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "WAIVED validation error reference:minimal_ahb_interconnect.cpu_m (1)" in proc.stdout
    assert (build / "rtl" / "minimal_soc.sv").is_file()
