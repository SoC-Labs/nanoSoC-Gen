# Copyright 2021-2026 SoC Labs (www.soclabs.org), University of Southampton
# SPDX-License-Identifier: Apache-2.0
"""Pad packs (soc_model/pad_packs.py): how foundry pad data is found, and that
a pad ring that was asked for and cannot be built is an error, not a skip.

A pack here is a scratch directory built from the bundled generic files under
another name, so nothing in these tests depends on a real foundry pack.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from soc_model import pad_packs
from soc_model.model import Module
from soc_model.parser import SoCParser
from soc_model.builder import SoCBuilder
from soc_model.backends.pad_ring import SoCPadRingBackend

from tests.conftest import FIXTURES
from tests.helpers import arm_free_env

pytestmark = pytest.mark.unit

PKG_ROOT = Path(__file__).resolve().parents[2]
LIB = PKG_ROOT / "lib"


def _make_pack(root: Path, tech="packtech", gpio_template=None, mixed_template=None):
    """A pad pack holding <tech> (= generic renamed) and <tech>_multicore."""
    (root / "pad_tech").mkdir(parents=True)
    (root / "pin_map").mkdir()
    pt = yaml.safe_load((LIB / "pad_tech" / "generic.yaml").read_text())
    pt["pad_tech"]["name"] = tech
    (root / "pad_tech" / f"{tech}.yaml").write_text(yaml.safe_dump(pt, sort_keys=False))
    pm = (LIB / "pin_map" / "generic_multicore.yaml").read_text()
    (root / "pin_map" / f"{tech}_multicore.yaml").write_text(
        pm.replace("tech:         generic", f"tech:         {tech}"))
    if gpio_template is not None or mixed_template is not None:
        (root / "templates").mkdir()
    if gpio_template is not None:
        (root / "templates" / "nanosoc_chip_pads_asic.v.j2").write_text(gpio_template)
    if mixed_template is not None:
        (root / "templates" / "nanosoc_chip_pads_mixed.v.j2").write_text(mixed_template)
    return root


def _cli(args, env_extra=None):
    env = arm_free_env()
    env.pop(pad_packs.ENV_VAR, None)
    env.pop("PAD_TECH", None)
    env.pop("PIN_COUNT", None)
    env.update(PYTHONPATH=str(PKG_ROOT), PYTHONDONTWRITEBYTECODE="1")
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-m", "soc_model", *map(str, args)],
                          cwd=str(PKG_ROOT), env=env, capture_output=True, text=True,
                          timeout=300)


# ---------------------------------------------------------------------------
# search order
# ---------------------------------------------------------------------------
def test_pack_dirs_cli_first_then_env(tmp_path, monkeypatch):
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    monkeypatch.setenv(pad_packs.ENV_VAR, os.pathsep.join([str(b), str(a), ""]))
    assert pad_packs.pack_dirs([c, a]) == [c.resolve(), a.resolve(), b.resolve()]


def test_parser_finds_pack_tech_via_env(tmp_path, monkeypatch):
    pack = _make_pack(tmp_path / "pack")
    parser = SoCParser(str(FIXTURES), [str(FIXTURES)])
    monkeypatch.delenv(pad_packs.ENV_VAR, raising=False)
    assert parser.parse_pad_tech("packtech") is None
    monkeypatch.setenv(pad_packs.ENV_VAR, str(pack))
    assert parser.parse_pad_tech("packtech")["pad_tech"]["name"] == "packtech"
    assert parser.parse_pin_map("packtech_multicore")["pin_map"]["tech"] == "packtech"


def test_parser_finds_pack_tech_via_argument(tmp_path, monkeypatch):
    pack = _make_pack(tmp_path / "pack")
    monkeypatch.delenv(pad_packs.ENV_VAR, raising=False)
    parser = SoCParser(str(FIXTURES), [str(FIXTURES)], pad_tech_dirs=[str(pack)])
    assert parser.parse_pad_tech("packtech") is not None


def test_lib_dir_wins_over_pack(tmp_path, monkeypatch):
    """A project's own copy (--lib-dir) is found before a pack's."""
    pack = _make_pack(tmp_path / "pack")
    proj = _make_pack(tmp_path / "proj")
    pt = yaml.safe_load((proj / "pad_tech" / "packtech.yaml").read_text())
    pt["pad_tech"]["gpio_tio"] = 7
    (proj / "pad_tech" / "packtech.yaml").write_text(yaml.safe_dump(pt))
    monkeypatch.setenv(pad_packs.ENV_VAR, str(pack))
    parser = SoCParser(str(FIXTURES), [str(proj)])
    assert parser.parse_pad_tech("packtech")["pad_tech"]["gpio_tio"] == 7


# ---------------------------------------------------------------------------
# templates from a pack
# ---------------------------------------------------------------------------
def _gpio_backend(pack_dirs):
    """A GPIO-bus (fixed-pin) ring: its template is not bundled."""
    parser = SoCParser(str(FIXTURES), [str(FIXTURES)])
    b = SoCBuilder(parser)
    pad_tech = b._build_pad_tech(parser.parse_pad_tech("generic")["pad_tech"])
    pm = yaml.safe_load((LIB / "pin_map" / "generic_multicore.yaml").read_text())["pin_map"]
    pm.pop("pads"); pm["mode"] = "gpio"; pm["pin_count"] = 28
    pin_map = b._build_pin_map(pm)
    system = Module(name="gpio_chip_system")
    return SoCPadRingBackend(system, None, pad_tech, pin_map, pad_tech_dirs=pack_dirs)


def test_fixed_pin_ring_without_pack_template_is_an_error(tmp_path, monkeypatch):
    monkeypatch.delenv(pad_packs.ENV_VAR, raising=False)
    with pytest.raises(pad_packs.PadPackError) as e:
        _gpio_backend([]).generate(tmp_path / "rtl")
    assert "nanosoc_chip_pads_asic.v.j2" in str(e.value)
    assert "--pad-tech-dir" in str(e.value)
    assert not (tmp_path / "rtl").exists() or not any((tmp_path / "rtl").iterdir())


def test_fixed_pin_ring_uses_pack_template(tmp_path, monkeypatch):
    monkeypatch.delenv(pad_packs.ENV_VAR, raising=False)
    pack = _make_pack(tmp_path / "pack",
                      gpio_template="// from the pack: {{ module_name }} {{ pad_tech.name }}\n")
    outs = _gpio_backend([str(pack)]).generate(tmp_path / "rtl")
    v = next(p for p in outs if p.suffix == ".v")
    assert v.read_text() == "// from the pack: gpio_chip_chip_pads generic\n"


def test_pack_cannot_replace_a_bundled_template(tmp_path, monkeypatch):
    pack = _make_pack(tmp_path / "pack", mixed_template="// impostor\n")
    monkeypatch.setenv(pad_packs.ENV_VAR, str(pack))
    parser = SoCParser(str(FIXTURES), [str(FIXTURES)])
    b = SoCBuilder(parser)
    be = SoCPadRingBackend(
        Module(name="nanosoc_multicore_soc"), None,
        b._build_pad_tech(parser.parse_pad_tech("packtech")["pad_tech"]),
        b._build_pin_map(parser.parse_pin_map("packtech_multicore")["pin_map"]))
    text = next(p for p in be.generate(tmp_path / "rtl") if p.suffix == ".v").read_text()
    assert "impostor" not in text
    assert "Template:  nanosoc_chip_pads_mixed.v.j2" in text


# ---------------------------------------------------------------------------
# the command line: asked-for and missing = exit 1, before anything is written
# ---------------------------------------------------------------------------
def test_cli_missing_pad_tech_fails_before_writing(tmp_path):
    out = tmp_path / "out"
    r = _cli([FIXTURES / "minimal_soc.yaml", "--lib-dir", FIXTURES, "--build-dir", out,
              "--pad-tech", "packtech", "--pin-count", "multicore"])
    assert r.returncode == 1, r.stdout[-2000:]
    assert "pad tech 'packtech' not found" in r.stderr
    assert "NANOSOC_PAD_TECH_DIR" in r.stderr
    assert not out.exists()


def test_cli_missing_pin_map_fails(tmp_path):
    r = _cli([FIXTURES / "minimal_soc.yaml", "--lib-dir", FIXTURES,
              "--build-dir", tmp_path / "out", "--pad-tech", "generic", "--pin-count", "99"])
    assert r.returncode == 1
    assert "pin map 'generic_99pin' not found" in r.stderr


def test_cli_pad_tech_needs_pin_count(tmp_path):
    r = _cli([FIXTURES / "minimal_soc.yaml", "--lib-dir", FIXTURES,
              "--build-dir", tmp_path / "out", "--pad-tech", "generic"])
    assert r.returncode == 1
    assert "needs --pin-count" in r.stderr


def test_cli_pack_via_option_and_env(tmp_path):
    pack = _make_pack(tmp_path / "pack")
    for how in ("option", "env"):
        out = tmp_path / f"out_{how}"
        args = [FIXTURES / "minimal_soc.yaml", "--lib-dir", FIXTURES, "--build-dir", out,
                "--pad-tech", "packtech", "--pin-count", "multicore"]
        if how == "option":
            r = _cli(args + ["--pad-tech-dir", pack])
        else:
            r = _cli(args, {pad_packs.ENV_VAR: str(pack)})
        assert r.returncode == 0, (how, r.stderr[-2000:])
        assert (out / "rtl" / "nanosoc_chip_pads_packtech_multicore.v").exists(), how


def test_cli_pad_tech_none_is_rtl_only(tmp_path):
    out = tmp_path / "out"
    r = _cli([FIXTURES / "minimal_soc.yaml", "--lib-dir", FIXTURES, "--build-dir", out],
             {"PAD_TECH": "none", "PIN_COUNT": "multicore"})
    assert r.returncode == 0, r.stderr[-2000:]
    assert not list((out / "rtl").glob("nanosoc_chip_pads_*"))
