# Copyright 2026, SoC Labs (www.soclabs.org)
"""Unit tests for ``soc_model.parser.SoCParser``.

Covers the YAML system-description parser at the model seam: construction /
lib_dir normalisation, ``parse_top_level`` + ``_load_yaml`` error handling,
``parse_module`` with its lazy recursive scan + dual name/stem cache +
last-writer-wins collision semantics, ``parse_register_map`` search order,
``parse_interface_definition`` (zero call sites in production — exercised
directly here) including the bundled-interfaces fallback, and
``list_module_files``.

All tests are pure-Python and use ``tmp_path`` for hermetic on-disk fixtures.
``_BUNDLED_INTERFACES_DIR`` (the in-repo ``lib/interfaces/``) is read only,
never written.

Several plan-asserted behaviours were re-validated against the REAL code +
installed PyYAML 8.3.5 / repo state before pinning here; where the actual
behaviour differs from the design doc the ACTUAL behaviour is pinned and the
divergence is noted inline:
  * duplicate YAML keys do NOT raise (PyYAML safe_load keeps last) -> last
    value wins, not ``{}`` (see test_load_yaml_duplicate_keys_last_wins);
  * the bundled interface YAMLs use ``${...}`` Jinja templating that is NOT
    valid flow-mapping YAML, so the bundled-fallback branch returns ``{}``
    (a real ``_load_yaml`` result, distinct from the early ``None``) rather
    than a populated dict (see test_interface_def_bundled_fallback).
"""

import re
from pathlib import Path

import pytest
import yaml

from soc_model.parser import SoCParser, _BUNDLED_INTERFACES_DIR

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# small local helpers (NOT the shared helpers.py — these write tmp YAML)
# ---------------------------------------------------------------------------
def _w(path: Path, text: str) -> Path:
    """Write ``text`` to ``path`` (creating parents) and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _module_yaml(name: str = "", **extra: str) -> str:
    """Render a minimal ``module:`` YAML body, optionally with a name."""
    lines = ["module:"]
    if name:
        lines.append(f"  name: {name}")
    for k, v in extra.items():
        lines.append(f"  {k}: {v}")
    if len(lines) == 1:  # bare 'module:' would be null; give it a port list
        lines.append("  ports: []")
    return "\n".join(lines) + "\n"


# ===========================================================================
# __init__ (parser.py:33)
# ===========================================================================
class TestInit:
    def test_lib_dir_none_defaults_to_base(self, tmp_path):
        # P-INIT-01
        p = SoCParser(str(tmp_path), None)
        assert p.lib_dirs == [Path(tmp_path)]
        assert p.lib_dir == Path(tmp_path)
        assert p.base_dir == Path(tmp_path)

    def test_lib_dir_str_single_element_list(self, tmp_path):
        # P-INIT-02
        p = SoCParser(str(tmp_path), "/some/lib")
        assert p.lib_dirs == [Path("/some/lib")]
        assert p.lib_dir == Path("/some/lib")

    def test_lib_dir_list_each_coerced_to_path(self, tmp_path):
        # P-INIT-03 (list)
        a, b = "/lib/a", "/lib/b"
        p = SoCParser(str(tmp_path), [a, b])
        assert p.lib_dirs == [Path(a), Path(b)]
        assert p.lib_dir == Path(a)  # first kept as primary

    def test_lib_dir_tuple_each_coerced_to_path(self, tmp_path):
        # P-INIT-03 (tuple)
        a, b = "/lib/a", "/lib/b"
        p = SoCParser(str(tmp_path), (a, b))
        assert p.lib_dirs == [Path(a), Path(b)]
        assert p.lib_dir == Path(a)

    def test_base_dir_coerced_and_caches_empty_unscanned(self):
        # P-INIT-04
        p = SoCParser("rel/base")
        assert isinstance(p.base_dir, Path)
        assert p.base_dir == Path("rel/base")
        assert p._module_cache == {}
        assert p._modules_scanned is False

    def test_empty_lib_dir_list_raises_indexerror(self):
        # P-INIT-05 FOOT-GUN: lib_dir = lib_dirs[0] on []
        with pytest.raises(IndexError):
            SoCParser("/base", [])


# ===========================================================================
# parse_top_level (parser.py:50) + _load_yaml (parser.py:170)
# ===========================================================================
class TestParseTopLevel:
    def test_valid_top_yaml_returns_dict(self, tmp_path):
        # P-TOP-01
        _w(tmp_path / "top.yaml", _module_yaml("my_soc"))
        p = SoCParser(str(tmp_path))
        data = p.parse_top_level("top.yaml")
        assert isinstance(data, dict)
        assert "module" in data
        assert data["module"]["name"] == "my_soc"

    def test_missing_file_returns_empty_with_warning(self, tmp_path, capsys):
        # P-TOP-02: {} and a 'File not found' warning, no raise
        p = SoCParser(str(tmp_path))
        assert p.parse_top_level("nope.yaml") == {}
        out = capsys.readouterr().out
        assert "Warning: File not found:" in out
        assert "nope.yaml" in out

    def test_malformed_yaml_returns_empty_with_warning(self, tmp_path, capsys):
        # P-TOP-03
        _w(tmp_path / "bad.yaml", "a: [1, 2")
        p = SoCParser(str(tmp_path))
        assert p.parse_top_level("bad.yaml") == {}
        assert "Warning: YAML parse error" in capsys.readouterr().out

    def test_empty_file_returns_empty_no_warning(self, tmp_path, capsys):
        # P-TOP-04: empty file -> {} with NO warning
        _w(tmp_path / "empty.yaml", "")
        p = SoCParser(str(tmp_path))
        assert p.parse_top_level("empty.yaml") == {}
        assert capsys.readouterr().out == ""

    def test_top_without_module_key(self, tmp_path):
        # P-TOP-05
        _w(tmp_path / "t.yaml", "foo: bar\n")
        p = SoCParser(str(tmp_path))
        data = p.parse_top_level("t.yaml")
        assert data == {"foo": "bar"}
        assert "module" not in data


class TestLoadYaml:
    @pytest.mark.parametrize("text", ["null", "0", "[]", "{}", ""])
    def test_falsy_results_become_empty_dict(self, tmp_path, text):
        # P-LY-01: FOOT-GUN — falsy parses (incl []) all collapse to {}
        f = _w(tmp_path / "f.yaml", text)
        p = SoCParser(str(tmp_path))
        assert p._load_yaml(f) == {}

    def test_truthy_list_returned_unchanged(self, tmp_path):
        # P-LY-02 (list)
        f = _w(tmp_path / "l.yaml", "[1, 2]")
        assert SoCParser(str(tmp_path))._load_yaml(f) == [1, 2]

    def test_truthy_int_returned_unchanged(self, tmp_path):
        # P-LY-02 (int)
        f = _w(tmp_path / "i.yaml", "42")
        assert SoCParser(str(tmp_path))._load_yaml(f) == 42

    def test_load_yaml_duplicate_keys_last_wins(self, tmp_path, capsys):
        # P-LY-03 (pinned to ACTUAL behaviour): the plan expected a YAMLError
        # -> {} here, but PyYAML 8.3.5 safe_load silently keeps the LAST
        # duplicate key (no error, no warning). Pin the real behaviour.
        f = _w(tmp_path / "dup.yaml", "a: 1\na: 2\n")
        assert SoCParser(str(tmp_path))._load_yaml(f) == {"a": 2}
        assert capsys.readouterr().out == ""

    def test_directory_path_raises_isadirectory(self, tmp_path):
        # P-LY-04: only FileNotFoundError / YAMLError are caught; opening a
        # directory raises IsADirectoryError (NOT caught -> propagates).
        with pytest.raises(IsADirectoryError):
            SoCParser(str(tmp_path))._load_yaml(tmp_path)

    def test_include_tag_unsupported_yields_empty(self, tmp_path, capsys):
        # P-LY-05: !include is a ConstructorError (a YAMLError) -> {} + warn
        f = _w(tmp_path / "inc.yaml", "!include other.yaml")
        assert SoCParser(str(tmp_path))._load_yaml(f) == {}
        assert "Warning: YAML parse error" in capsys.readouterr().out


# ===========================================================================
# parse_module + scan + cache (parser.py:55/67/109)
# ===========================================================================
class TestParseModule:
    def test_found_by_name_and_by_stem_same_dict(self, tmp_path):
        # P-MOD-01: dual cache — name AND filename stem -> identical object
        _w(tmp_path / "myfile.yaml", _module_yaml("foo"))
        p = SoCParser(str(tmp_path))
        by_name = p.parse_module("foo")
        by_stem = p.parse_module("myfile")
        assert by_name is not None
        assert by_name is by_stem

    def test_unknown_name_returns_none_no_warning(self, tmp_path, capsys):
        # P-MOD-02
        p = SoCParser(str(tmp_path))
        assert p.parse_module("missing") is None
        assert capsys.readouterr().out == ""

    def test_lazy_scan_latch_file_added_later_not_seen(self, tmp_path):
        # P-MOD-03: scan happens once; the latch blocks re-scan
        _w(tmp_path / "first.yaml", _module_yaml("first"))
        p = SoCParser(str(tmp_path))
        assert p.parse_module("first") is not None
        assert p._modules_scanned is True
        _w(tmp_path / "second.yaml", _module_yaml("second"))
        assert p.parse_module("second") is None  # not re-scanned

    def test_module_without_name_cached_by_stem_only(self, tmp_path):
        # P-MOD-04: no name -> only stem-keyed
        _w(tmp_path / "anon.yaml", _module_yaml())  # no name
        p = SoCParser(str(tmp_path))
        assert p.parse_module("anon") is not None
        assert "module" in p.parse_module("anon")
        # nothing else got cached under an empty name
        assert "" not in p._module_cache

    def test_null_name_cached_by_stem_only(self, tmp_path):
        # P-MOD-04 variant: explicit null name -> '' -> stem only
        _w(tmp_path / "nm.yaml", "module:\n  name: null\n  ports: []\n")
        p = SoCParser(str(tmp_path))
        assert p.parse_module("nm") is not None
        assert "" not in p._module_cache

    def test_legacy_modules_dir_scanned(self, tmp_path):
        # P-MOD-05
        _w(tmp_path / "modules" / "m.yaml", _module_yaml("legacy_mod"))
        p = SoCParser(str(tmp_path))
        assert p.parse_module("legacy_mod") is not None

    def test_hierarchical_subsystems_and_regions_scanned(self, tmp_path):
        # P-MOD-06
        _w(tmp_path / "subsystems" / "cpu" / "c.yaml", _module_yaml("cpu_ss"))
        _w(tmp_path / "regions" / "r" / "r.yaml", _module_yaml("r_region"))
        p = SoCParser(str(tmp_path))
        assert p.parse_module("cpu_ss") is not None
        assert p.parse_module("r_region") is not None

    def test_ip_local_rtl_sys_desc_scanned(self, tmp_path):
        # P-MOD-07: base/../rtl/<ip>/sys_desc/*.yaml
        base = tmp_path / "sys_desc"
        _w(base / ".keep", "")
        _w(tmp_path / "rtl" / "myip" / "sys_desc" / "ip.yaml",
           _module_yaml("ip_mod"))
        p = SoCParser(str(base))
        assert p.parse_module("ip_mod") is not None

    def test_lib_dir_root_scanned(self, tmp_path):
        # P-MOD-08
        _w(tmp_path / "m.yaml", _module_yaml("root_mod"))
        p = SoCParser(str(tmp_path))
        assert p.parse_module("root_mod") is not None

    def test_hidden_yaml_skipped(self, tmp_path):
        # P-MOD-09 (:98 startswith('.') guard)
        _w(tmp_path / ".secret.yaml", _module_yaml("secret_mod"))
        p = SoCParser(str(tmp_path))
        assert p.parse_module("secret_mod") is None
        assert p.parse_module(".secret") is None

    def test_non_yaml_extensions_ignored(self, tmp_path):
        # P-MOD-10: .yml and .json are not globbed
        _w(tmp_path / "m.yml", _module_yaml("yml_mod"))
        _w(tmp_path / "m.json", '{"module": {"name": "json_mod"}}')
        p = SoCParser(str(tmp_path))
        assert p.parse_module("yml_mod") is None
        assert p.parse_module("json_mod") is None
        assert p.parse_module("m") is None

    def test_file_without_module_key_skipped_silently(self, tmp_path, capsys):
        # P-MOD-11
        _w(tmp_path / "plain.yaml", "foo: 1\n")
        p = SoCParser(str(tmp_path))
        assert p.parse_module("plain") is None
        assert capsys.readouterr().out == ""

    def test_empty_yaml_in_scan_dir_skipped(self, tmp_path):
        # P-MOD-12: 'if data' guard — empty file doesn't crash, isn't cached
        _w(tmp_path / "empty.yaml", "")
        _w(tmp_path / "real.yaml", _module_yaml("real_mod"))
        p = SoCParser(str(tmp_path))
        assert p.parse_module("real_mod") is not None
        assert p.parse_module("empty") is None

    def test_missing_special_dirs_no_crash(self, tmp_path):
        # P-MOD-13: no modules/subsystems/regions/rtl dirs -> is_dir guards
        _w(tmp_path / "only.yaml", _module_yaml("only_mod"))
        p = SoCParser(str(tmp_path))
        assert p.parse_module("only_mod") is not None  # lib_dir root still ok

    def test_multiple_lib_dirs_all_scanned(self, tmp_path):
        # P-MOD-14
        a = tmp_path / "a"
        b = tmp_path / "b"
        _w(a / "ma.yaml", _module_yaml("mod_a"))
        _w(b / "mb.yaml", _module_yaml("mod_b"))
        p = SoCParser(str(a), [str(a), str(b)])
        assert p.parse_module("mod_a") is not None
        assert p.parse_module("mod_b") is not None

    def test_collision_across_lib_dirs_last_writer_wins(self, tmp_path):
        # P-MOD-COLLIDE-01: lib_dirs scanned in list order -> b (last) wins
        a = tmp_path / "a"
        b = tmp_path / "b"
        _w(a / "ma.yaml", _module_yaml("foo", desc="from_a"))
        _w(b / "mb.yaml", _module_yaml("foo", desc="from_b"))
        p = SoCParser(str(a), [str(a), str(b)])
        assert p.parse_module("foo")["module"]["desc"] == "from_b"

    def test_collision_modules_vs_root_root_wins(self, tmp_path):
        # P-MOD-COLLIDE-02: within one lib_dir, modules/ is scanned BEFORE the
        # lib_dir root (root appended last) -> root payload wins. This is a
        # code-fixed dir ordering, not filesystem glob order.
        _w(tmp_path / "modules" / "x.yaml",
           _module_yaml("foo", desc="from_modules"))
        _w(tmp_path / "y.yaml", _module_yaml("foo", desc="from_root"))
        p = SoCParser(str(tmp_path))
        assert p.parse_module("foo")["module"]["desc"] == "from_root"

    def test_non_dict_module_value_raises_attributeerror(self, tmp_path):
        # P-MOD-FRAGILE-01: 'module: 5' -> mod_data.get() on an int during scan
        _w(tmp_path / "bad.yaml", "module: 5\n")
        p = SoCParser(str(tmp_path))
        with pytest.raises(AttributeError):
            p.parse_module("anything")  # scan runs on first call


# ===========================================================================
# parse_register_map (parser.py:114)
# ===========================================================================
class TestParseRegisterMap:
    def test_found_in_base_dir(self, tmp_path):
        # P-RM-01
        _w(tmp_path / "regs.yaml", "register_map:\n  name: r\n")
        p = SoCParser(str(tmp_path))
        rm = p.parse_register_map("regs.yaml")
        assert isinstance(rm, dict)
        assert rm["register_map"]["name"] == "r"

    def test_base_dir_precedence_over_lib_dir(self, tmp_path):
        # P-RM-02
        base = tmp_path / "base"
        lib = tmp_path / "lib"
        _w(base / "regs.yaml", "register_map:\n  src: base\n")
        _w(lib / "regs.yaml", "register_map:\n  src: lib\n")
        p = SoCParser(str(base), [str(lib)])
        assert p.parse_register_map("regs.yaml")["register_map"]["src"] == "base"

    def test_found_directly_in_lib_dir(self, tmp_path):
        # P-RM-03
        base = tmp_path / "base"
        lib = tmp_path / "lib"
        base.mkdir()
        _w(lib / "regs.yaml", "register_map:\n  src: lib\n")
        p = SoCParser(str(base), [str(lib)])
        assert p.parse_register_map("regs.yaml")["register_map"]["src"] == "lib"

    def test_found_via_register_maps_basename_fallback(self, tmp_path):
        # P-RM-04: 'register_maps/foo.yaml' resolved via lib/register_maps/foo.yaml
        base = tmp_path / "base"
        lib = tmp_path / "lib"
        base.mkdir()
        _w(lib / "register_maps" / "foo.yaml", "register_map:\n  src: rm\n")
        p = SoCParser(str(base), [str(lib)])
        rm = p.parse_register_map("register_maps/foo.yaml")
        assert rm["register_map"]["src"] == "rm"

    def test_not_found_returns_none_with_warning(self, tmp_path, capsys):
        # P-RM-05
        p = SoCParser(str(tmp_path))
        assert p.parse_register_map("ghost.yaml") is None
        assert "Warning: Register map not found:" in capsys.readouterr().out

    def test_empty_found_file_returns_empty_dict(self, tmp_path, capsys):
        # P-RM-06: empty {} distinguishes a FOUND-but-empty from not-found None
        _w(tmp_path / "regs.yaml", "")
        p = SoCParser(str(tmp_path))
        result = p.parse_register_map("regs.yaml")
        assert result == {}
        assert result is not None
        assert "Register map not found" not in capsys.readouterr().out

    def test_found_file_yaml_error_returns_empty_not_notfound(self, tmp_path, capsys):
        # P-RM-07: malformed FOUND file -> {} + YAML warning, NOT the
        # 'not found' warning (the early base_dir branch returned via _load_yaml)
        _w(tmp_path / "regs.yaml", "a: [1, 2")
        p = SoCParser(str(tmp_path))
        assert p.parse_register_map("regs.yaml") == {}
        out = capsys.readouterr().out
        assert "Warning: YAML parse error" in out
        assert "Register map not found" not in out


# ===========================================================================
# parse_interface_definition (parser.py:141) — ZERO production call sites
# ===========================================================================
class TestParseInterfaceDefinition:
    def test_found_directly_in_base_dir(self, tmp_path):
        # P-IF-01 (direct)
        _w(tmp_path / "myif.yaml", "interface:\n  src: base_direct\n")
        p = SoCParser(str(tmp_path))
        r = p.parse_interface_definition("myif.yaml")
        assert r["interface"]["src"] == "base_direct"

    def test_found_in_base_interfaces_subdir(self, tmp_path):
        # P-IF-01 (interfaces/ subdir)
        _w(tmp_path / "interfaces" / "myif.yaml",
           "interface:\n  src: base_subdir\n")
        p = SoCParser(str(tmp_path))
        r = p.parse_interface_definition("myif.yaml")
        assert r["interface"]["src"] == "base_subdir"

    def test_found_in_lib_dir_and_lib_interfaces(self, tmp_path):
        # P-IF-02
        base = tmp_path / "base"
        lib_a = tmp_path / "lib_a"
        lib_b = tmp_path / "lib_b"
        base.mkdir()
        _w(lib_a / "ifa.yaml", "interface:\n  src: lib_direct\n")
        _w(lib_b / "interfaces" / "ifb.yaml",
           "interface:\n  src: lib_subdir\n")
        p = SoCParser(str(base), [str(lib_a), str(lib_b)])
        assert p.parse_interface_definition("ifa.yaml")["interface"]["src"] == "lib_direct"
        assert p.parse_interface_definition("ifb.yaml")["interface"]["src"] == "lib_subdir"

    def test_base_direct_beats_base_interfaces_subdir(self, tmp_path):
        # P-IF-03: within the same base, the direct path is checked first
        _w(tmp_path / "myif.yaml", "interface:\n  src: direct\n")
        _w(tmp_path / "interfaces" / "myif.yaml",
           "interface:\n  src: subdir\n")
        p = SoCParser(str(tmp_path))
        assert p.parse_interface_definition("myif.yaml")["interface"]["src"] == "direct"

    def test_base_precedence_over_lib(self, tmp_path):
        # P-IF-03 (base before lib): search order is [base_dir] + lib_dirs
        base = tmp_path / "base"
        lib = tmp_path / "lib"
        _w(base / "myif.yaml", "interface:\n  src: base\n")
        _w(lib / "myif.yaml", "interface:\n  src: lib\n")
        p = SoCParser(str(base), [str(lib)])
        assert p.parse_interface_definition("myif.yaml")["interface"]["src"] == "base"

    def test_bundled_fallback_reached_when_absent_locally(self, tmp_path, capsys):
        # P-IF-04: with nothing local, the bundled lib/interfaces/ fallback
        # fires. NOTE (pinned to ACTUAL): the bundled YAMLs use ${...} Jinja
        # templating that is NOT valid flow-mapping YAML, so _load_yaml emits a
        # parse warning and returns {} — a REAL _load_yaml result, distinct
        # from the early `return None` of a true not-found. Asserting `is not
        # None` proves the bundled branch executed.
        p = SoCParser(str(tmp_path))
        r = p.parse_interface_definition("ahb_master.yaml")
        assert r is not None
        assert r == {}  # parse-error of the templated bundled file
        assert "Warning: YAML parse error" in capsys.readouterr().out

    def test_local_copy_shadows_bundled(self, tmp_path):
        # P-IF-05: a parseable local interfaces/ahb_master.yaml is returned in
        # preference to the bundled (broken) one — proves the local branch wins
        # AND yields real content.
        _w(tmp_path / "interfaces" / "ahb_master.yaml",
           "interface:\n  name: ahb_master\n  src: LOCAL_SENTINEL\n")
        p = SoCParser(str(tmp_path))
        r = p.parse_interface_definition("ahb_master.yaml")
        assert r["interface"]["src"] == "LOCAL_SENTINEL"

    def test_not_found_anywhere_returns_none_no_warning(self, tmp_path, capsys):
        # P-IF-06: this function NEVER warns; missing -> None, clean stdout
        p = SoCParser(str(tmp_path))
        assert p.parse_interface_definition("does_not_exist_anywhere.yaml") is None
        assert capsys.readouterr().out == ""

    def test_bundled_interfaces_dir_exists(self):
        # P-IF-07: the fallback branch is reachable in this repo
        assert _BUNDLED_INTERFACES_DIR.is_dir()


# ===========================================================================
# list_module_files (parser.py:163)
# ===========================================================================
class TestListModuleFiles:
    def test_returns_all_cache_keys_names_and_stems(self, tmp_path):
        # P-LIST-01: name==stem dedups (foo); distinct name (baz) + stem (bar)
        _w(tmp_path / "foo.yaml", _module_yaml("foo"))
        _w(tmp_path / "bar.yaml", _module_yaml("baz"))
        p = SoCParser(str(tmp_path))
        assert sorted(p.list_module_files()) == ["bar", "baz", "foo"]

    def test_triggers_scan_and_sets_latch(self, tmp_path):
        # P-LIST-02
        _w(tmp_path / "m.yaml", _module_yaml("m_mod"))
        p = SoCParser(str(tmp_path))
        assert p._modules_scanned is False
        names = p.list_module_files()
        assert names  # non-empty
        assert p._modules_scanned is True

    def test_empty_lib_returns_empty_list(self, tmp_path):
        # P-LIST-03
        p = SoCParser(str(tmp_path))
        assert p.list_module_files() == []


# ===========================================================================
# Cross-cutting
# ===========================================================================
class TestCrossCutting:
    def test_all_warnings_go_to_stdout_stderr_clean(self, tmp_path, capsys):
        # P-X-01: every warning path uses print() -> capsys.out; err empty
        p = SoCParser(str(tmp_path))
        # 1) File not found (top level)
        p.parse_top_level("missing.yaml")
        # 2) YAML parse error
        _w(tmp_path / "bad.yaml", "a: [1, 2")
        p.parse_top_level("bad.yaml")
        # 3) Register map not found
        SoCParser(str(tmp_path)).parse_register_map("ghost.yaml")
        cap = capsys.readouterr()
        assert "Warning: File not found:" in cap.out
        assert "Warning: YAML parse error" in cap.out
        assert "Warning: Register map not found:" in cap.out
        assert cap.err == ""

    def test_repeated_parse_module_returns_cached_object(self, tmp_path):
        # P-X-02
        _w(tmp_path / "m.yaml", _module_yaml("cached_mod"))
        p = SoCParser(str(tmp_path))
        r1 = p.parse_module("cached_mod")
        r2 = p.parse_module("cached_mod")
        assert r1 is r2

    def test_base_dir_not_scanned_when_lib_dir_given(self, tmp_path):
        # P-X-03: providing lib_dirs replaces the default [base], so a module
        # living only in base_dir is NOT discovered.
        base = tmp_path / "base"
        other = tmp_path / "other"
        other.mkdir()
        _w(base / "m.yaml", _module_yaml("base_only_mod"))
        p = SoCParser(str(base), [str(other)])
        assert p.parse_module("base_only_mod") is None
