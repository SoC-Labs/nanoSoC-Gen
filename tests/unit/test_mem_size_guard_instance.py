# Copyright 2026, SoC Labs (www.soclabs.org)
"""The discovery size guard reads the instance CONNECTED to a target (Train 7 G6).

A target's `instance:` used to be trusted: when it named a generated
subsystem the target was a WINDOW (not checked here), whatever was actually
wired to it. Plant: sram_0's `instance:` says u_ss_cpu while u_region_sram_0 is
connected to it -- a RAM whose size nobody checked. Now a WINDOW needs the
connected instance to be the subsystem, and `instance:` must agree with it.
"""
import pytest

from tests.unit.test_mem_size_guard import _line, _run, _setup

pytestmark = pytest.mark.unit

RAM_T = 'name: ram_0, instance: u_ram_0,'
WIN = ("region_type: ~, role: ~, desc: \"Subsystem window\"",
       "region_type: memory, role: ~, desc: \"Subsystem window\"")


def _edit(d, *pairs):
    y = d / "mem_guard_soc.yaml"
    t = y.read_text()
    for old, new in pairs:
        assert t.count(old) == 1, old
        t = t.replace(old, new)
    y.write_text(t)


def test_instance_naming_a_subsystem_is_not_a_window_when_a_ram_is_wired(tmp_path):
    d, fl = _setup(tmp_path, "2 ** $ROM_W", ram="4 * (2 ** $RAM_W)", wrapper="byte")
    _edit(d, (RAM_T, 'name: ram_0, instance: u_ss_0,'))
    r, out = _run(d, fl)
    line = _line(out, "ram_0")
    assert r.returncode == 1, out[-3000:]
    assert line.startswith("UNRESOLVED"), line
    assert "`instance: u_ss_0` disagrees with the connection: u_ram_0 is connected" in line
    assert "0 subsystem window(s)" in out


def test_instance_naming_nothing_is_unresolved(tmp_path):
    d, fl = _setup(tmp_path, "2 ** $ROM_W", wrapper="byte")
    _edit(d, (RAM_T, 'name: ram_0, instance: u_ram_9,'))
    r, out = _run(d, fl)
    assert r.returncode == 1
    assert "`instance: u_ram_9` names no instance of mem_guard_soc" in _line(out, "ram_0")


def test_window_when_the_connected_subsystem_agrees(tmp_path):
    d, fl = _setup(tmp_path, "2 ** $ROM_W", wrapper="byte")
    _edit(d, WIN, ('name: ss_0_slave, base:', 'name: ss_0_slave, instance: u_ss_0, base:'))
    r, out = _run(d, fl)
    assert r.returncode == 0, out[-3000:]
    assert _line(out, "ss_0_slave").startswith("WINDOW")
    assert "3 match, 0 mismatch, 0 unresolved, 1 subsystem window(s), 0 waived" in out


def test_window_needs_a_connection(tmp_path):
    """`instance:` names the subsystem, but the subsystem's port is wired elsewhere."""
    d, fl = _setup(tmp_path, "2 ** $ROM_W", wrapper="byte")
    _edit(d, WIN, ('name: ss_0_slave, base:', 'name: ss_0_slave, instance: u_ss_0, base:'),
          ('- { port: sub_s,       conn: mem_guard_ic.ss_0_slave }',
           '- { port: sub_s,       conn: unconnected }'))
    r, out = _run(d, fl)
    line = _line(out, "ss_0_slave")
    assert r.returncode == 1, out[-3000:]
    assert line.startswith("UNRESOLVED") and "alone does not make a target a window" in line
