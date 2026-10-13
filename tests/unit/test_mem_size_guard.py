# Copyright 2026, SoC Labs (www.soclabs.org)
"""The discovery size guard: a discovery table must report the size of the
memory compiled behind each target, and a render that does not is refused.

Planted defects, each one a form that reached silicon or a release:
  * a RAM over-reported 2x, 4x and 8x ("4 * (2 ** $W)" on a BYTE width: eth
    rc4 CPU1 IMEM/DMEM, nanosoc_m0_soc; 8x when a subsystem default was read
    instead of the top's override), and a RAM under-reported;
  * a boot ROM sized with the wrong unit for ITS wrapper: "4 * (2 ** N)"
    behind a byte slice HADDR[N-1:2] (rc4 CPU1, 4x), "2 ** N" behind
    bootrom_gen's word slice HADDR[N+1:2] (1/4);
  * a ROM shallower than its decode window (it repeats; the table must not
    claim the window).
And what the guard must not do: fail a correct render, size a boot ROM by a
unit rule when its wrapper is unknown, or pick between two definitions of a
module.

Fixture: tests/fixtures/mem_guard (a RAM, a boot ROM, and a gen:True
subsystem whose RAM width the top overrides 15 -> 13).
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tests.helpers import arm_free_env

pytestmark = pytest.mark.unit

PKG_ROOT = Path(__file__).resolve().parents[2]
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "mem_guard"

GOOD = {"RAM": "2 ** $RAM_W", "SS": "2 ** $IMEM_W"}


def _setup(tmp_path, rom_phys, ram="2 ** $RAM_W", ss="2 ** $IMEM_W", wrapper=None):
    """A copy of the fixture with the three phys_size formulas filled in, and
    (wrapper='byte'|'word'|'both') a file list naming the RTL the guard reads."""
    d = tmp_path / "soc"
    shutil.copytree(FIX, d)
    for p in [d / "mem_guard_soc.yaml", d / "subsystems" / "guard_ss" / "guard_ss.yaml"]:
        t = p.read_text().replace("@RAM_PHYS@", ram).replace("@ROM_PHYS@", rom_phys).replace("@SS_PHYS@", ss)
        p.write_text(t)
    flist = None
    if wrapper:
        rtl = d / "rtl"
        files = [rtl / "guard_ram_region.v", rtl / "sl_ahb_sram.v"]
        if wrapper in ("byte", "both"):
            files += [rtl / "guard_rom_region_byte.v", rtl / "guard_rom9.sv"]
        if wrapper in ("word", "both"):
            files += [rtl / "guard_rom_region_word.v", rtl / "guard_rom11.sv"]
        if wrapper == "word9":                       # word slice over a 512-word ROM
            (rtl / "w9.v").write_text((rtl / "guard_rom_region_word.v").read_text()
                                      .replace("guard_rom11", "guard_rom9"))
            files += [rtl / "w9.v", rtl / "guard_rom9.sv"]
        flist = d / "compile.flist"
        flist.write_text("// guard fixture compile list\n+libext+.v+.sv\n"
                         + "".join(f"$(GUARD_FIX)/rtl/{f.name}\n" for f in files))
    return d, flist


def _run(d, flist=None, *extra):
    build = d / "build_soc"
    env = arm_free_env()
    env.update(PYTHONPATH=str(PKG_ROOT), PYTHONDONTWRITEBYTECODE="1", GUARD_FIX=str(d))
    cmd = [sys.executable, "-m", "soc_model", str(d / "mem_guard_soc.yaml"),
           "--lib-dir", str(d), "--build-dir", str(build)]
    if flist:
        cmd += ["--rtl-flist", str(flist)]
    r = subprocess.run(cmd + list(extra), cwd=str(PKG_ROOT), env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, universal_newlines=True, timeout=600)
    return r, r.stdout + r.stderr


def _line(out, target):
    hits = [l.strip() for l in out.splitlines() if f".{target}:" in l and
            l.strip().split(' ', 1)[0] in ("OK", "MISMATCH", "UNRESOLVED", "WINDOW", "WAIVED")]
    assert len(hits) == 1, out[-3000:]
    return hits[0]


# --- clean ----------------------------------------------------------------------
@pytest.mark.parametrize("wrapper, rom", [("byte", "2 ** $ROM_W"), ("word", "4 * (2 ** $ROM_W)")])
def test_correct_sizes_pass(tmp_path, wrapper, rom):
    d, fl = _setup(tmp_path, rom, wrapper=wrapper)
    r, out = _run(d, fl)
    assert r.returncode == 0, out[-3000:]
    assert _line(out, "ram_0").startswith("OK") and "table 0x1000, compiled 0x1000" in _line(out, "ram_0")
    want = "0x800" if wrapper == "byte" else "0x2000"
    assert _line(out, "rom_0").startswith("OK") and f"table {want}, compiled {want}" in _line(out, "rom_0")
    # the subsystem's RAM is the top's override (13), not its default (15)
    assert _line(out, "imem_0").startswith("OK") and "compiled 0x2000" in _line(out, "imem_0")
    assert "3 match, 0 mismatch, 0 unresolved, 0 subsystem window(s), 0 waived" in out


# --- RAM over/under-reports -----------------------------------------------------
@pytest.mark.parametrize("ram, table, ratio", [
    ("2 * (2 ** $RAM_W)", "0x2000", "2x"),
    ("4 * (2 ** $RAM_W)", "0x4000", "4x"),      # the rc4 / nanosoc_m0_soc form
    ("8 * (2 ** $RAM_W)", "0x8000", "8x"),
    ("2 ** ($RAM_W - 2)", "0x400", "1/4"),      # under-report
])
def test_ram_size_mismatch_fails(tmp_path, ram, table, ratio):
    d, fl = _setup(tmp_path, "2 ** $ROM_W", ram=ram, wrapper="byte")
    r, out = _run(d, fl)
    assert r.returncode == 1, out[-3000:]
    line = _line(out, "ram_0")
    assert line.startswith("MISMATCH") and f"table {table}, compiled 0x1000" in line
    assert f"the table says {ratio}" in line
    assert "discovery-table size(s) differ from the memory compiled behind them" in out


def test_subsystem_default_instead_of_override_fails(tmp_path):
    """The 8x mechanism: a size from the subsystem's default width (15) while
    the top passes 13 is 4x the compiled RAM here (and 8x in rc4)."""
    d, fl = _setup(tmp_path, "2 ** $ROM_W", ss="2 ** ($IMEM_W + 2)", wrapper="byte")   # = 2 ** 15, the default
    r, out = _run(d, fl)
    assert r.returncode == 1
    line = _line(out, "imem_0")
    assert line.startswith("MISMATCH") and "table 0x8000, compiled 0x2000" in line


def test_ram_checked_without_a_file_list(tmp_path):
    """No --rtl-flist: a RAM region is still sized, by its RAM_ADDR_W."""
    d, _ = _setup(tmp_path, "2 ** $ROM_W", ram="4 * (2 ** $RAM_W)")
    r, out = _run(d, None, "--mem-size-waive", "mem_guard_ic.rom_0=sized by the firmware flow")
    assert r.returncode == 1
    assert _line(out, "ram_0").startswith("MISMATCH")
    assert "RAM_ADDR_W=12, the sl_ahb_sram BYTE width" in _line(out, "ram_0")


# --- boot ROMs: the unit is the wrapper's -------------------------------------------
@pytest.mark.parametrize("wrapper, rom, table, compiled, ratio", [
    ("byte", "4 * (2 ** $ROM_W)", "0x2000", "0x800", "4x"),     # rc4 CPU1: 4x behind a byte slice
    ("word", "2 ** $ROM_W", "0x800", "0x2000", "1/4"),          # arch_tech main's form behind bootrom_gen's
    ("word9", "4 * (2 ** $ROM_W)", "0x2000", "0x800", "4x"),    # an 11-bit decode over a 512-word ROM
])
def test_rom_unit_mismatch_fails(tmp_path, wrapper, rom, table, compiled, ratio):
    d, fl = _setup(tmp_path, rom, wrapper=wrapper)
    r, out = _run(d, fl)
    assert r.returncode == 1, out[-3000:]
    line = _line(out, "rom_0")
    assert line.startswith("MISMATCH") and f"table {table}, compiled {compiled}" in line, line
    assert f"the table says {ratio}" in line


def test_rom_slice_and_depth_are_read_not_assumed(tmp_path):
    d, fl = _setup(tmp_path, "4 * (2 ** $ROM_W)", wrapper="word")
    r, out = _run(d, fl)
    line = _line(out, "rom_0")
    assert "slices HADDR[ROM_ADDR_W+1:2] = [12:2] (11-bit word address)" in line
    assert "guard_rom11 word_addr[11-1:0] (11 bits)" in line


def test_rom_without_its_wrapper_fails_closed(tmp_path):
    """No compile list: the ROM's unit is unknown, so it is NOT guessed and NOT
    passed: UNRESOLVED fails the run (whichever formula the YAML has), and the
    message names the one-argument fix."""
    for rom in ("2 ** $ROM_W", "4 * (2 ** $ROM_W)"):
        d, _ = _setup(tmp_path / rom.replace(' ', '').replace('*', 'x'), rom)
        r, out = _run(d)
        assert r.returncode == 1, out[-3000:]
        line = _line(out, "rom_0")
        assert line.startswith("UNRESOLVED") and "--rtl-flist" in line and "--mem-size-waive" in line
        assert "1 unresolved" in out and "cannot be established" in out


def test_two_definitions_of_a_wrapper_are_not_resolved(tmp_path):
    """Both wrapper forms in one compile list: the check cannot know which one
    the compile takes, so it does not pick -- and that fails the run."""
    d, fl = _setup(tmp_path, "2 ** $ROM_W", wrapper="both")
    r, out = _run(d, fl)
    assert r.returncode == 1
    line = _line(out, "rom_0")
    assert line.startswith("UNRESOLVED") and "defined in 2 files of the compile list" in line


def test_off_chip_window_is_unresolved_until_waived(tmp_path):
    """A memory target nothing compiled sizes (here: the RAM region's source is
    listed but holds no RAM or ROM, as qspi_flash_ahb's XIP window) fails,
    and a per-target waiver with its reason lets it through, visibly."""
    d, fl = _setup(tmp_path, "2 ** $ROM_W", wrapper="byte")
    (d / "rtl" / "guard_ram_region.v").write_text(
        "module guard_ram_region #(parameter SYS_ADDR_W = 32, parameter SYS_DATA_W = 32,\n"
        "  parameter RAM_ADDR_W = 12) (input wire HCLK, input wire HRESETn,\n"
        "  input wire [SYS_ADDR_W-1:0] HADDR);\nendmodule\n")
    r, out = _run(d, fl)
    assert r.returncode == 1
    assert _line(out, "ram_0").startswith("UNRESOLVED") and "no RAM or ROM in guard_ram_region" in _line(out, "ram_0")
    # the subsystem's imem_0 is the same (empty) module: waived on its own line
    r, out = _run(d, fl, "--mem-size-waive", "mem_guard_ic.ram_0=off-chip: the device sizes it")
    assert r.returncode == 1 and _line(out, "imem_0").startswith("UNRESOLVED")
    r, out = _run(d, fl, "--mem-size-waive", "mem_guard_ic.ram_0=off-chip: the device sizes it",
                  "--mem-size-waive", "guard_ss_ic.imem_0=same module")
    assert r.returncode == 0, out[-3000:]
    line = _line(out, "ram_0")
    assert line.startswith("WAIVED") and "[waived: off-chip: the device sizes it; without the waiver: UNRESOLVED]" in line
    assert ("2 waived [mem_guard_ic.ram_0: off-chip: the device sizes it (found UNRESOLVED); "
            "guard_ss_ic.imem_0: same module (found UNRESOLVED)]") in out


def test_subsystem_window_is_classified_not_unchecked(tmp_path):
    """A memory-typed target onto a generated subsystem is a WINDOW (its own
    memories are checked at its own interconnect), not a silent pass."""
    d, fl = _setup(tmp_path, "2 ** $ROM_W", wrapper="byte")
    y = d / "mem_guard_soc.yaml"
    t = y.read_text()
    old = "region_type: ~, role: ~, desc: \"Subsystem window\""
    assert t.count(old) == 1
    y.write_text(t.replace(old, "region_type: memory, role: ~, desc: \"Subsystem window\""))
    r, out = _run(d, fl)
    assert r.returncode == 0, out[-3000:]
    assert _line(out, "ss_0_slave").startswith("WINDOW")
    assert "1 subsystem window(s)" in out


def test_waiver_on_a_mismatch_reports_and_passes(tmp_path):
    d, fl = _setup(tmp_path, "2 ** $ROM_W", ram="4 * (2 ** $RAM_W)", wrapper="byte")
    r, out = _run(d, fl, "--mem-size-waive", "mem_guard_ic.ram_0=frozen rc4 render")
    assert r.returncode == 0
    line = _line(out, "ram_0")
    assert line.startswith("WAIVED") and "the table says 4x the memory" in line
    assert "without the waiver: MISMATCH" in line and "1 waived [mem_guard_ic.ram_0: frozen rc4 render" in out


@pytest.mark.parametrize("arg, rc, why", [
    ("mem_guard_ic.ram_0", 2, "expected <interconnect>.<target>=<reason>"),       # no reason
    ("mem_guard_ic.ram_0=", 2, "expected <interconnect>.<target>=<reason>"),      # empty reason
    ("ram_0=why", 2, "expected <interconnect>.<target>=<reason>"),                # no interconnect
    ("mem_guard_ic.nope_0=why", 1, "--mem-size-waive mem_guard_ic.nope_0: no memory target"),
])
def test_bad_waiver_refused(tmp_path, arg, rc, why):
    d, fl = _setup(tmp_path, "2 ** $ROM_W", wrapper="byte")
    r, out = _run(d, fl, "--mem-size-waive", arg)
    assert r.returncode == rc and why in out, out[-2000:]


def test_unneeded_waiver_fails(tmp_path):
    """A waiver naming a target that already matches is stale (Train 7 G7):
    it would hide the next defect on that target, so the run fails."""
    d, fl = _setup(tmp_path, "2 ** $ROM_W", wrapper="byte")
    r, out = _run(d, fl, "--mem-size-waive", "mem_guard_ic.ram_0=just in case")
    assert r.returncode == 1, out[-3000:]
    assert "the sizes agree: this waiver is not needed" in _line(out, "ram_0")
    assert ("ERROR: --mem-size-waive mem_guard_ic.ram_0: a stale waiver: the sizes agree "
            "(found OK). Remove the waiver.") in out


def test_waiver_on_a_window_fails(tmp_path):
    d, fl = _setup(tmp_path, "2 ** $ROM_W", wrapper="byte")
    y = d / "mem_guard_soc.yaml"
    y.write_text(y.read_text().replace("region_type: ~, role: ~, desc: \"Subsystem window\"",
                                       "region_type: memory, role: ~, desc: \"Subsystem window\""))
    r, out = _run(d, fl, "--mem-size-waive", "mem_guard_ic.ss_0_slave=why not")
    assert r.returncode == 1, out[-3000:]
    assert "mem_guard_ic.ss_0_slave: a stale waiver: it is a subsystem window" in out


def test_global_switch_is_gone(tmp_path):
    d, fl = _setup(tmp_path, "2 ** $ROM_W", ram="4 * (2 ** $RAM_W)", wrapper="byte")
    r, out = _run(d, fl, "--allow-mem-size-mismatch")
    assert r.returncode == 2 and "unrecognized arguments: --allow-mem-size-mismatch" in out


def test_config_override_is_what_compiles(tmp_path):
    """--config-override RAM_W=13: the chip passes 13, the table says 0x2000,
    and the guard sizes the RAM at 13 too."""
    d, fl = _setup(tmp_path, "2 ** $ROM_W", wrapper="byte")
    r, out = _run(d, fl, "--config-override", "RAM_W=13")
    assert r.returncode == 0, out[-3000:]
    assert "table 0x2000, compiled 0x2000" in _line(out, "ram_0")


# --- the pieces -------------------------------------------------------------------
def test_eval_const():
    from soc_model.backends.mem_size_guard import eval_const, try_const
    env = {"W": 11, "P": 0x80000001}
    assert eval_const("W+1", env) == 12 and eval_const("W-1", env) == 10
    assert eval_const("4 * 2 ** W", env) == 8192 and eval_const("(1 << W) - 1", env) == 2047
    assert eval_const("32'h10 + 'd2 + 8'b1", env) == 19
    assert eval_const("$clog2(2048)", env) == 11 and eval_const("P[0]", env) == 1
    assert eval_const("W ? 7 : 9", env) == 7
    for bad in ("X + 1", '"image.hex"', "1'bx", "W +"):
        assert try_const(bad, env) is None


def test_read_flist_expands_and_reports(tmp_path, monkeypatch):
    from soc_model.backends.mem_size_guard import read_flist
    (tmp_path / "a.v").write_text("module a; endmodule\n")
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "b.sv").write_text("module b; endmodule\n")
    (tmp_path / "inner.f").write_text("$(TOPD)/a.v\n")
    (tmp_path / "top.f").write_text("// c\n+incdir+x\n-f ${TOPD}/inner.f\n-y $TOPD/lib\n"
                                    "$(NOT_SET_GUARD_VAR)/c.v\n$(TOPD)/missing.v\n")
    monkeypatch.setenv("TOPD", str(tmp_path))
    monkeypatch.delenv("NOT_SET_GUARD_VAR", raising=False)
    notes = []
    files = read_flist(tmp_path / "top.f", notes)
    assert [f.name for f in files] == ["a.v", "b.sv"]
    assert any("$NOT_SET_GUARD_VAR (1 list(s))" in n for n in notes)
    assert any("missing.v" in n for n in notes)
