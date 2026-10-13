# Copyright 2026, SoC Labs (www.soclabs.org)
"""--mem-size-check-only: the discovery size guard after the firmware build (Train 7 G5).

Flows that build the boot ROM after soc_model (eth-ss standalone: `bootloader:
soc_model`, `bootrom: bootloader`; MPS3) cannot give the render the ROM
wrapper the guard reads: it does not exist yet. They render with the one ROM
waived, build the ROM, then check the existing build directory against the
compile list with --mem-size-check-only, which renders and writes nothing.
"""
import hashlib

import pytest

from tests.unit.test_mem_size_guard import _line, _run, _setup

pytestmark = pytest.mark.unit

WAIVE_ROM = ("--mem-size-waive", "mem_guard_ic.rom_0=the firmware builds the ROM after the render")


def _tree(build):
    return {p.relative_to(build): (p.stat().st_mtime_ns, hashlib.md5(p.read_bytes()).hexdigest())
            for p in sorted(build.rglob("*")) if p.is_file()}


def test_render_then_check_after_the_rom_is_built(tmp_path):
    d, fl = _setup(tmp_path, "4 * (2 ** $ROM_W)", wrapper="word")
    r, out = _run(d, None, *WAIVE_ROM)                     # render: no ROM wrapper yet
    assert r.returncode == 0, out[-3000:]
    assert _line(out, "rom_0").startswith("WAIVED")
    before = _tree(d / "build_soc")
    r, out = _run(d, fl, "--mem-size-check-only")           # the ROM is built: check it
    assert r.returncode == 0, out[-3000:]
    assert _line(out, "rom_0").startswith("OK") and "table 0x2000, compiled 0x2000" in _line(out, "rom_0")
    assert "3 match, 0 mismatch, 0 unresolved, 0 subsystem window(s), 0 waived" in out
    assert "--- AHB Interconnect Generation ---" not in out and "HSEL-reader" not in out
    assert _tree(d / "build_soc") == before                 # nothing written


def test_check_only_finds_the_rom_the_table_misreports(tmp_path):
    d, fl = _setup(tmp_path, "2 ** $ROM_W", wrapper="word")   # byte formula, word wrapper: 1/4
    r, out = _run(d, None, *WAIVE_ROM)
    assert r.returncode == 0, out[-3000:]
    r, out = _run(d, fl, "--mem-size-check-only")
    assert r.returncode == 1
    assert _line(out, "rom_0").startswith("MISMATCH") and "1 mismatch" in out


def test_check_only_keeps_the_waiver_rules(tmp_path):
    d, fl = _setup(tmp_path, "4 * (2 ** $ROM_W)", wrapper="word")
    assert _run(d, None, *WAIVE_ROM)[0].returncode == 0
    r, out = _run(d, fl, "--mem-size-check-only", *WAIVE_ROM)   # the ROM matches now: stale
    assert r.returncode == 1 and "mem_guard_ic.rom_0: a stale waiver" in out


def test_check_only_needs_a_render(tmp_path):
    d, fl = _setup(tmp_path, "2 ** $ROM_W", wrapper="byte")
    r, out = _run(d, fl, "--mem-size-check-only")
    assert r.returncode == 1 and "has no discovery/ or rtl/: render it first" in out
    assert not (d / "build_soc").exists()


def test_check_only_renders_nothing_else(tmp_path):
    d, fl = _setup(tmp_path, "2 ** $ROM_W", wrapper="byte")
    r, out = _run(d, fl, "--mem-size-check-only", "--validate-only")
    assert r.returncode == 2 and "--mem-size-check-only renders nothing" in out
