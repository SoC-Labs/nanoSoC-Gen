# Copyright 2026, SoC Labs (www.soclabs.org)
"""YAML 1.1 integers that do not mean what they say are refused (review
R-TRAIN5, LOW).

PyYAML reads an unquoted `020000000` as OCTAL (4194304) and `1:00` as BASE 60
(60). R-TRAIN5 showed both landing in a linker ORIGIN with no message. For an
integer-typed key the parser now refuses a plain scalar with a leading zero
(other than 0, 0x.., 0o.., 0b..) or a colon; quoting keeps a string a string.
The loader itself is not changed: everything else constructs exactly as
yaml.safe_load builds it.
"""
import shutil
from pathlib import Path

import pytest
import yaml

from helpers import build_module_from_yaml

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
GEN_ROOT = Path(__file__).resolve().parents[2]


def _yi():
    from soc_model import yaml_ints
    return yaml_ints


def _minimal(tmp_path, old, new):
    for f in FIXTURES.iterdir():
        if f.is_dir():
            shutil.copytree(f, tmp_path / f.name)
    src = (FIXTURES / "minimal_soc.yaml").read_text()
    assert src.count(old) == 1, old
    (tmp_path / "minimal_soc.yaml").write_text(src.replace(old, new))
    return "minimal_soc.yaml"


# --- refused in a real model ------------------------------------------------------
@pytest.mark.parametrize("old, new, why", [
    ("phys_size: 0x00001000", "phys_size: 010000", "phys_size: 010000 is not read as written .*octal: 4096"),
    ("base: 0x00000000, size: 0x00001000", "base: 0x00000000, size: 1:00",
     "size: 1:00 is not read as written .*base 60: 60"),
    ("SRAM_RAM_ADDR_W: { type: int, default: 12,", "SRAM_RAM_ADDR_W: { type: int, default: 014,",
     "params.SRAM_RAM_ADDR_W.default: 014 .*octal: 12"),
    ("RAM_ADDR_W: $SRAM_RAM_ADDR_W }", "RAM_ADDR_W: 014 }", "params.RAM_ADDR_W: 014 "),
    ("WIDTH: 1 }, desc: \"AHB clock in\"", "WIDTH: 01 }, desc: \"AHB clock in\"", "params.WIDTH: 01 "),
])
def test_misread_integer_refused(tmp_path, old, new, why):
    top = _minimal(tmp_path, old, new)
    with pytest.raises(ValueError, match=why):
        build_module_from_yaml(tmp_path, top)


def test_linker_origin_octal_and_base60_refused(fixtures_dir, tmp_path):
    """The two R-TRAIN5 probes: `origin: 020000000` linked at 0x0040_0000 and
    `origin: 1:00` at 0x3C."""
    from test_linker_origin_refusals import _soc
    for origin, why in (("020000000", "octal: 4194304"), ("1:00", "base 60: 60")):
        d = tmp_path / origin.replace(':', '_')
        d.mkdir()
        region = "{ target: dmem_0, linker_name: T, origin: %s, phys_size: 0x4000 }" % origin
        with pytest.raises(ValueError, match=f"origin: {origin} is not read as written .*{why}"):
            build_module_from_yaml(d, _soc(fixtures_dir, d, region).name)


# --- octal and base 60 are refused under ANY key (review R-TRAIN6) -------------------
@pytest.mark.parametrize("text, why", [
    ("value: 010", "value: 010 .*octal: 8"),                  # glue constant: rendered .VALUE(9'd8)
    ("build_info: {fields: [{name: SOC_VERSION, value: 0100}]}", "octal: 64"),   # was SOC_VERSION 0x40
    ("params: {V: {type: str, default: 0123}}", "params.V.default: 0123 .*octal: 83"),  # a string param, int 83
    ("desc: 1:00", "desc: 1:00 .*base 60: 60"),
    ("name: 010", "octal: 8"),
    ("knobs: [{min: 01, max: 07, default: 02}]", "octal: 1"),   # component knobs
    ("010: a", "010 \\(as a key\\)"),
    ("x: 00", "octal: 0"),
])
def test_octal_and_base60_refused_under_any_key(text, why):
    with pytest.raises(_yi().YamlIntegerError, match=why):
        _yi().load_yaml_checked(text, "t.yaml")


def test_glue_constant_octal_refused_in_a_model(tmp_path):
    """End to end: a glue constant `value: 010` (which rendered .VALUE(9'd8))
    stops the build."""
    top = _minimal(tmp_path, "  interconnects:\n",
                   "  internal_wires:\n    - { name: c9, type: wire, params: { WIDTH: 9 } }\n"
                   "  glue_logic:\n    - { name: k, type: constant, value: 010, width: 9, output: c9 }\n"
                   "  interconnects:\n")
    with pytest.raises(ValueError, match="glue_logic.\\[0\\].value: 010 is not read as written"):
        build_module_from_yaml(tmp_path, top)


# --- what stays accepted ----------------------------------------------------------
@pytest.mark.parametrize("text, want", [
    ("size: 0", {"size": 0}),
    ("size: 0x1000", {"size": 4096}),
    ("size: 0b101", {"size": 5}),
    ("size: 4096", {"size": 4096}),
    ("size: 4_096", {"size": 4096}),
    ('size: "020"', {"size": "020"}),            # quoted: the author asked for text
    ("origin: '1:00'", {"origin": "1:00"}),
    ("freq_hz: 0.5", {"freq_hz": 0.5}),
    ("desc: 08", {"desc": "08"}),                 # a leading-zero STRING outside an integer key: text
    ("name: '010'", {"name": "010"}),
    ("params: {IMG: {type: str, default: '0123'}}", {"params": {"IMG": {"type": "str", "default": "0123"}}}),
])
def test_accepted_scalars_load_as_yaml11(text, want):
    assert _yi().load_yaml_checked(text, "t.yaml") == want == yaml.safe_load(text)


def test_integer_keys_come_from_the_model():
    keys = _yi().INTEGER_KEYS
    for k in ("base", "size", "phys_size", "origin", "size_adjust", "width", "offset",
              "reset_value", "bit", "freq_hz", "pad_zero_above", "tie_off"):
        assert k in keys, k
    for k in ("name", "desc", "module", "type", "constraints"):
        assert k not in keys, k


def test_checked_loader_builds_what_safe_load_builds():
    """Every YAML in this repository's fixtures and libraries constructs to the
    same data through the checked loader as through yaml.safe_load."""
    n = 0
    for root in (FIXTURES, GEN_ROOT / "lib"):
        for p in sorted(root.rglob("*.yaml")):
            text = p.read_text()
            try:
                want = yaml.safe_load(text)
            except yaml.YAMLError:
                continue
            assert _yi().load_yaml_checked(text, str(p)) == want, p
            n += 1
    # a floor, so a scan that finds nothing cannot pass: 49 files at Train 9
    # (the 11 foundry pad YAMLs moved to the pad pack; 2 generic ones added)
    assert n > 40
