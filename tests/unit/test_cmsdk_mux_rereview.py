# Copyright 2026, SoC Labs (www.soclabs.org)
"""``impl: cmsdk_mux``: the second independent review (R-ICX-2, 2026-09-27).

F3: a misspelt key on a target, an initiator's target entry, a visibility
window or a remap spec was ignored and wrong RTL was written with exit 0
(``passthru`` even recreated the deadlock topology), and a quoted
``remapping: "false"`` read as true. Each is now refused with a
did-you-mean. F5: the misspelt-``impl`` rule refused harmless keys (``ip`` on
a bus matrix, ``implementation_notes`` on a cmsdk_mux interconnect); it now
matches only one edit from ``impl`` (or a cmsdk_mux value), and a bus-matrix
interconnect never gains a refusal it did not have before.

Case ids (p01 ...) are the review's probes.
"""

import re

import pytest

from soc_model.backends import cmsdk_mux
from soc_model.backends.ahb import SoCAhbBackend
from soc_model.backends.cmsdk_mux import plan_cmsdk_mux
from soc_model.validator import SoCValidator

from tests.unit.test_cmsdk_mux_refusals import (
    IC, _assert_refused, _case, _cli, _ic, _ic_errors, _ic_of, _ini, _tgt, _base, _write)

pytestmark = pytest.mark.unit


def _vis(d, init, target):
    return next(x for x in _ini(d, init)["targets"] if x["name"] == target)


# ---------------------------------------------------------------------------
# F3: misspelt nested keys and a quoted boolean
# ---------------------------------------------------------------------------
def _p01(d):                  # remapping: "false" (a string): read as true
    for i in ("cpu_m", "ext_s"):
        _vis(d, i, "ram_0")["visibility"][0]["remap"]["remapping"] = "false"


def _p06(d):                  # remaping: the window was silently always visible
    v = _vis(d, "ext_s", "dat_0")
    v["visibility"] = [{"base": 0x18000000},
                       {"base": 0x60000000,
                        "remap": {"remaping": True, "remap_behaviour": "alias", "remap_bit": 1}}]


def _p07(d):                  # remap_behavior (US spelling): a move became an alias
    for i in ("cpu_m", "ext_s"):
        r = _vis(d, i, "ram_0")["visibility"][0]["remap"]
        del r["remap_behaviour"]
        r["remap_behavior"] = "move"


def _p08(d):                  # aperature: the window silently took the target's size
    _vis(d, "ext_s", "rom_0")["visibility"] = [{"base": 0x0},
                                               {"base": 0x40000000, "aperature": 0x1000}]


def _p09(d):                  # passthru: 'system' went onto the shared bus
    t = _tgt(d, "system")
    t["passthru"] = t.pop("passthrough")


@pytest.mark.parametrize("edit, pattern", [
    (_p01, r"initiator 'cpu_m', target 'ram_0', visibility\[0\] has remapping: 'false' "
           r"\(str\); it must be true or false, unquoted"),
    (_p06, r"initiator 'ext_s', target 'dat_0', visibility\[1\]\.remap has unknown key "
           r"'remaping' \(value True\); did you mean 'remapping'\?"),
    (_p07, r"initiator 'cpu_m', target 'ram_0', visibility\[0\]\.remap has unknown key "
           r"'remap_behavior' \(value 'move'\); did you mean 'remap_behaviour'\?"),
    (_p08, r"initiator 'ext_s', target 'rom_0', visibility\[1\] has unknown key "
           r"'aperature' \(value 1073741824\)|initiator 'ext_s', target 'rom_0', "
           r"visibility\[1\] has unknown key 'aperature' \(value 4096\); did you mean "
           r"'aperture'\?"),
    (_p09, r"target 'system' has unknown key 'passthru' \(value 'sys_m'\); did you "
           r"mean 'passthrough'\?"),
], ids=["p01_remapping_string", "p06_remaping", "p07_remap_behavior",
        "p08_aperature", "p09_passthru"])
def test_misspelt_nested_key_is_refused(fixtures_dir, tmp_path, edit, pattern):
    top = _case(fixtures_dir, tmp_path, edit)
    _assert_refused(top, tmp_path, pattern)


@pytest.mark.parametrize("edit", [_p01, _p06, _p07, _p08, _p09],
                         ids=["p01", "p06", "p07", "p08", "p09"])
def test_misspelt_nested_key_stops_the_cli(fixtures_dir, tmp_path, edit):
    d = _base(fixtures_dir)
    edit(d)
    build = tmp_path / "build"
    proc = _cli(_write(d, tmp_path / "yaml"), build, fixtures_dir)
    assert proc.returncode != 0, proc.stdout[-2000:]
    assert "ERROR: refusing to generate" in proc.stderr, proc.stderr
    assert not (list(build.rglob("*_lite.v")) if build.exists() else [])


def test_passthru_would_have_put_system_on_the_shared_bus(fixtures_dir, tmp_path):
    """Why p09 matters: with the key ignored, 'system' is no passthrough
    target, so it sits on the shared bus with the passthrough initiator
    ext_s: the cross-interconnect deadlock topology."""
    top = _case(fixtures_dir, tmp_path, _p09)
    t = next(x for x in _ic_of(top).targets if x.name == "system")
    assert t.passthrough is None
    assert getattr(t, "_unknown_yaml_keys", {}) == {"passthru": "sys_m"}


def test_unknown_nested_key_that_is_no_misspelling_is_a_warning(fixtures_dir, tmp_path):
    def edit(d):
        _tgt(d, "dat_0")["colour"] = "blue"
        _vis(d, "cpu_m", "ram_0")["visibility"][1]["note"] = "alias window"
        _ini(d, "ext_s")["targets"][0]["access"] = "ro"
    top = _case(fixtures_dir, tmp_path, edit)
    plan_cmsdk_mux(_ic_of(top))
    warns = sorted(m.message for m in SoCValidator(top).validate_all()
                   if m.level == "warning" and m.category == "interconnect")
    assert warns == sorted([
        f"{IC}: target 'dat_0' has unknown key 'colour'; it is ignored",
        f"{IC}: initiator 'cpu_m', target 'ram_0', visibility[1] has unknown key 'note'; it is ignored",
        f"{IC}: initiator 'ext_s', target 'rom_0' has unknown key 'access'; it is ignored",
    ]), warns


def test_quoted_true_is_refused_and_unquoted_bools_accepted(fixtures_dir, tmp_path):
    def quoted(d):
        _vis(d, "cpu_m", "ram_0")["visibility"][0]["remap"]["remapping"] = "true"
    _assert_refused(_case(fixtures_dir, tmp_path / "q", quoted), tmp_path / "q",
                    r"has remapping: 'true' \(str\)")

    def unquoted_false(d):    # ext_s: ram_0 also plainly at 0x6000_0000
        v = _vis(d, "ext_s", "ram_0")["visibility"][0]
        v["remap"]["remapping"] = False
        v["base"] = 0x60000000
    plan = plan_cmsdk_mux(_ic_of(_case(fixtures_dir, tmp_path / "f", unquoted_false)))
    assert "(b_dec_a == 5'hc)" in plan["shared_exprs"]["RAM_0"], plan["shared_exprs"]["RAM_0"]


# ---------------------------------------------------------------------------
# F5: harmless keys are no longer refused; bus-matrix behaviour unchanged
# ---------------------------------------------------------------------------
def test_p12_key_ip_on_a_bus_matrix_is_not_refused(fixtures_dir, tmp_path):
    def edit(d):
        _ic(d)["impl"] = "busmatrix"
        _ic(d)["ip"] = "vendor note"
    top = _case(fixtures_dir, tmp_path, edit)
    assert _ic_errors(top) == []
    assert cmsdk_mux.check_cmsdk_mux(_ic_of(top)) == []


def test_p13_implementation_notes_on_cmsdk_mux_is_only_a_warning(fixtures_dir, tmp_path):
    def edit(d):
        _ic(d)["implementation_notes"] = "see docs"
    top = _case(fixtures_dir, tmp_path, edit)
    assert _ic_errors(top) == []
    warns = [m.message for m in SoCValidator(top).validate_all()
             if m.level == "warning" and m.category == "interconnect"]
    assert warns == [f"{IC}: unknown interconnect key 'implementation_notes' is ignored"]
    plan_cmsdk_mux(_ic_of(top))
    SoCAhbBackend(top).generate_all(tmp_path / "build")
    assert list((tmp_path / "build").rglob("*_lite.v"))


@pytest.mark.parametrize("key, value", [("imlp", "busmatrix"), ("imp", "yes"), ("Impl", "x")],
                         ids=["swap_busmatrix_value", "imp_other_value", "case"])
def test_impl_like_key_on_a_bus_matrix_is_only_noted(fixtures_dir, tmp_path, key, value):
    """A bus-matrix interconnect builds exactly as before (0950612 ignored the
    key); only a value of cmsdk_mux (c19) makes it an error."""
    def edit(d):
        del _ic(d)["impl"]
        _ic(d)[key] = value
    top = _case(fixtures_dir, tmp_path, edit)
    assert cmsdk_mux.ic_impl(_ic_of(top)) == "busmatrix"
    assert _ic_errors(top) == []


def test_impl_like_key_contradicting_cmsdk_mux_is_refused(fixtures_dir, tmp_path):
    def edit(d):
        _ic(d)["imp"] = "busmatrix"
    top = _case(fixtures_dir, tmp_path, edit)
    errs = _ic_errors(top)
    assert len(errs) == 1 and re.search(r"unknown interconnect key 'imp' \(value 'busmatrix'\)",
                                        errs[0].message), [str(m) for m in errs]


@pytest.mark.parametrize("key, hit", [
    ("imp", True), ("Impl", True), ("ipml", True), ("impel", True), ("mpl", True),
    ("ip", False), ("implementation_notes", False), ("implementation", False),
    ("simple", False), ("type", False)])
def test_impl_key_match_is_one_edit(key, hit):
    assert cmsdk_mux._looks_like_impl_key(key) is hit
