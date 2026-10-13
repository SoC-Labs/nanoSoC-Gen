# Copyright 2026, SoC Labs (www.soclabs.org)
"""Unit tests for ``soc_model/utils.py`` (foundation tier).

Pure Python; only ``write_if_changed`` touches the filesystem (via
``tmp_path``). No EDA tools, no jinja2 path, sub-ms each. Every assertion is
grounded against the real code in ``soc_model/utils.py`` and the
``tests/TEST_PLAN.md`` §5.1 matrix (test IDs carried in the docstrings /
ids= so a row maps to exactly one assertion).

Foot-guns deliberately *pinned* (current behaviour, documented as such):
  * substring corruption in ``resolve_param_ref`` when one param name is a
    prefix of another and substituted first (U-RP-11),
  * dict/list-in-list NOT resolved by ``resolve_params_in_dict`` (U-RD-04),
  * ``write_if_changed`` trailing-newline-only diff -> no rewrite (U-WC-09),
  * block-comment ``/* Generated ... */`` NOT stripped (U-ST-06).
"""

import re

import pytest

from soc_model.utils import (
    resolve_param_ref,
    _safe_eval,
    resolve_params_in_dict,
    parse_bit_slice,
    bit_slice_width,
    parse_conn_ref,
    get_param_value,
    flatten_params,
    write_if_changed,
    _strip_timestamps,
    _TIMESTAMP_LINE_RE,
)

pytestmark = pytest.mark.unit


# ===========================================================================
# resolve_param_ref (utils.py:8) -- 7 branches
# ===========================================================================

class TestResolveParamRef:
    @pytest.mark.parametrize("value", [5, None, [1, 2], 3.14, True])
    def test_non_str_passthrough(self, value):
        """U-RP-01: non-str passes through unchanged (identity)."""
        assert resolve_param_ref(value, {}) is value

    def test_pure_ref_native_typed(self):
        """U-RP-02: pure ``$X`` in params -> native-typed value (int)."""
        out = resolve_param_ref("$SYS_ADDR_W", {"SYS_ADDR_W": 32})
        assert out == 32
        assert isinstance(out, int)

    def test_pure_ref_zero_value(self):
        """U-RP-03: value 0 returned (membership check, not truthiness)."""
        assert resolve_param_ref("$Z", {"Z": 0}) == 0

    def test_pure_ref_non_int_verbatim(self):
        """U-RP-04: pure ``$X`` non-int returned verbatim."""
        assert resolve_param_ref("$F", {"F": True}) is True
        assert resolve_param_ref("$N", {"N": "foo"}) == "foo"

    def test_pure_ref_unresolved_passthrough(self):
        """U-RP-05: unresolved pure ``$X`` -> the literal string back."""
        assert resolve_param_ref("$MISSING", {}) == "$MISSING"

    def test_expr_power(self):
        """U-RP-06: arithmetic ``2 ** $N`` -> evaluated int."""
        assert resolve_param_ref("2 ** $N", {"N": 3}) == 8

    def test_expr_nested_parens(self):
        """U-RP-07: nested parens ``4 * (2 ** $N)`` -> 32."""
        assert resolve_param_ref("4 * (2 ** $N)", {"N": 3}) == 32

    def test_expr_unresolved_dollar_left(self):
        """U-RP-08: expr still containing ``$`` after sub -> substituted str."""
        assert resolve_param_ref("$N + $X", {"N": 3}) == "3 + $X"

    def test_expr_eval_rejected_returns_str(self):
        """U-RP-09: substituted expr the eval-whitelist rejects (``%``) -> str.

        This hits the regex-reject path INSIDE _safe_eval (no exception), so
        resolve_param_ref returns the (already $-free) substituted string."""
        assert resolve_param_ref("$N % 2", {"N": 5}) == "5 % 2"

    def test_expr_eval_raises_caught_returns_substituted_str(self):
        """U-RP-09b: covers the ``except Exception`` arm of resolve_param_ref.

        When the substituted expr is all-digits/operators it PASSES
        ``_safe_eval``'s regex but is not a valid expression, so ``eval``
        raises -- resolve_param_ref swallows it and returns the substituted
        string. (Distinct from U-RP-09, where _safe_eval's regex rejects and
        no exception is raised.)"""
        # SyntaxError path: "5 -" is whitelist-clean but not parseable.
        assert resolve_param_ref("$A -", {"A": 5}) == "5 -"
        # ZeroDivisionError path: "1 / 0" is whitelist-clean, raises at eval.
        assert resolve_param_ref("$A / $B", {"A": 1, "B": 0}) == "1 / 0"

    def test_expr_division_float(self):
        """U-RP-10: division -> float."""
        assert resolve_param_ref("8 / $N", {"N": 3}) == pytest.approx(2.6666666, rel=1e-6)

    def test_expr_substring_corruption_pinned(self):
        """U-RP-11: FOOT-GUN. When a shorter name (``$N``) is substituted
        before a longer one (``$NAME``) it corrupts the longer token. Pinned
        with an explicitly-ordered dict so the ordering is deterministic."""
        # N substituted first: "$NAME" -> "3AME" (the literal $N inside $NAME)
        assert resolve_param_ref("$NAME + 1", {"N": 3, "NAME": "foo"}) == "3AME + 1"
        # Opposite order is benign (NAME substituted first, no residual $N).
        assert resolve_param_ref("$NAME + 1", {"NAME": "foo", "N": 3}) == "foo + 1"

    def test_plain_str_unchanged(self):
        """U-RP-12: plain str with no ``$`` unchanged; empty str unchanged."""
        assert resolve_param_ref("hello", {}) == "hello"
        assert resolve_param_ref("", {}) == ""

    def test_dollar_space_not_word(self):
        """U-RP-13: ``$ `` fails the ``$\\w+`` fullmatch, enters the expr
        loop, nothing substitutes, no ``$``-strip -> returned as-is."""
        assert resolve_param_ref("$ ", {}) == "$ "


# ===========================================================================
# _safe_eval (utils.py:41) -- regex whitelist + eval; eval raise PROPAGATES
# ===========================================================================

class TestSafeEval:
    @pytest.mark.parametrize("expr,expected", [
        ("2 + 3", 5),
        ("2 ** 8", 256),
        ("4 * (2 ** 3)", 32),
    ])
    def test_arithmetic(self, expr, expected):
        """U-SE-01: add / power / nested."""
        assert _safe_eval(expr) == expected

    def test_true_division_and_whitespace(self):
        """U-SE-02: true division -> float; surrounding whitespace ok."""
        assert _safe_eval("8 / 2") == 4.0
        assert _safe_eval("  7  ") == 7

    def test_right_assoc_power(self):
        """U-SE-03: power is right-associative (2**(3**2))."""
        assert _safe_eval("2**3**2") == 512

    def test_unary_minus(self):
        """U-SE-04: unary minus inside the expression."""
        assert _safe_eval("-5 + 10") == 5

    @pytest.mark.parametrize("expr", ["abc", "2 + a"])
    def test_reject_identifier(self, expr):
        """U-SE-05: any identifier fails the regex -> input returned unchanged."""
        assert _safe_eval(expr) == expr

    @pytest.mark.parametrize("expr", ["__import__('os')", "print(1)"])
    def test_security_no_execution(self, expr):
        """U-SE-06: dangerous calls rejected by regex -> str back, no side-effect.

        (Letters/quotes fail the digits/ops whitelist, so ``eval`` is never
        reached -- and even if it were, ``__builtins__`` is emptied.)"""
        assert _safe_eval(expr) == expr

    def test_reject_modulo(self):
        """U-SE-07: ``%`` is not in the whitelist -> input back."""
        assert _safe_eval("2 % 3") == "2 % 3"

    @pytest.mark.parametrize("expr", ["0x10", "1<<2", "1e3", "[1]", "1,2"])
    def test_reject_hex_shift_sci_list_comma(self, expr):
        """U-SE-08: hex / shift / scientific / list / comma all rejected."""
        assert _safe_eval(expr) == expr

    def test_whitelist_pass_but_eval_raises_propagates(self):
        """U-SE-09: ``"-"`` passes the regex but is not a valid expression ->
        SyntaxError PROPAGATES (no try/except in _safe_eval)."""
        with pytest.raises(SyntaxError):
            _safe_eval("-")

    def test_div_by_zero_propagates(self):
        """U-SE-10: div-by-zero passes regex, raises at eval -> propagates."""
        with pytest.raises(ZeroDivisionError):
            _safe_eval("1/0")

    def test_abs_rejected_by_regex(self):
        """U-SE-11: ``abs(-1)`` has letters -> rejected by regex before eval."""
        assert _safe_eval("abs(-1)") == "abs(-1)"


# ===========================================================================
# resolve_params_in_dict (utils.py:49)
# ===========================================================================

class TestResolveParamsInDict:
    def test_flat_scalar(self):
        """U-RD-01: flat scalar value resolved."""
        assert resolve_params_in_dict({"w": "$N"}, {"N": 8}) == {"w": 8}

    def test_nested_dict_recursion(self):
        """U-RD-02: nested dict recursed into."""
        assert resolve_params_in_dict({"sub": {"w": "$N"}}, {"N": 8}) == {"sub": {"w": 8}}

    def test_list_scalars_elementwise(self):
        """U-RD-03: scalar list elements resolved element-wise."""
        assert resolve_params_in_dict({"l": ["$N", 5, "x"]}, {"N": 8}) == {"l": [8, 5, "x"]}

    def test_dict_or_list_inside_list_not_resolved(self):
        """U-RD-04: FOOT-GUN. dict / list nested *inside* a list are left
        untouched (the comprehension only resolves non-(dict,list) items)."""
        out = resolve_params_in_dict({"l": [{"w": "$N"}, [1]]}, {"N": 8})
        assert out == {"l": [{"w": "$N"}, [1]]}

    def test_list_arithmetic_element(self):
        """U-RD-05: arithmetic expr as a list element is evaluated."""
        assert resolve_params_in_dict({"l": ["2 ** $N"]}, {"N": 3}) == {"l": [8]}

    def test_no_input_mutation(self):
        """U-RD-06: returns a new dict; input is not mutated."""
        src = {"w": "$N", "sub": {"x": "$N"}}
        snapshot = {"w": "$N", "sub": {"x": "$N"}}
        resolve_params_in_dict(src, {"N": 8})
        assert src == snapshot


# ===========================================================================
# parse_bit_slice (utils.py:62) -- 3 branches
# ===========================================================================

class TestParseBitSlice:
    def test_range(self):
        """U-BS-01: range high:low."""
        assert parse_bit_slice("cpu_0_irq[31:16]") == ("cpu_0_irq", 31, 16)

    def test_single_bit(self):
        """U-BS-02: single bit -> high==low."""
        assert parse_bit_slice("p1_in[7]") == ("p1_in", 7, 7)
        assert parse_bit_slice("a[0]") == ("a", 0, 0)

    def test_no_slice(self):
        """U-BS-03: no slice -> (name, None, None)."""
        assert parse_bit_slice("sys_hclk") == ("sys_hclk", None, None)

    def test_dotted_name_retained(self):
        """U-BS-04: a dotted name with no slice keeps the dots in base."""
        assert parse_bit_slice("u_ss_cpu.sys_hclk") == ("u_ss_cpu.sys_hclk", None, None)

    def test_dotted_with_range(self):
        """U-BS-05: dotted base + range slice."""
        assert parse_bit_slice("u_ss.P1_OUT_MUX[6:4]") == ("u_ss.P1_OUT_MUX", 6, 4)

    def test_low_greater_than_high_not_validated(self):
        """U-BS-06: low>high is NOT validated -- pinned as-is."""
        assert parse_bit_slice("a[3:7]") == ("a", 3, 7)

    def test_three_part_slice_no_match(self):
        """U-BS-07: ``[1:2:3]`` doesn't match either regex -> whole base."""
        assert parse_bit_slice("weird[1:2:3]") == ("weird[1:2:3]", None, None)

    @pytest.mark.parametrize("sig", ["a[3]x", "name[]"])
    def test_trailing_chars_or_empty_brackets(self, sig):
        """U-BS-08: ``$`` anchor means trailing chars / empty ``[]`` -> base whole."""
        assert parse_bit_slice(sig) == (sig, None, None)

    def test_double_bracket_non_greedy(self):
        """U-BS-09: non-greedy first group -> base ``a[1]``, slice 2,2."""
        assert parse_bit_slice("a[1][2]") == ("a[1]", 2, 2)


# ===========================================================================
# bit_slice_width (utils.py:84)
# ===========================================================================

class TestBitSliceWidth:
    def test_range_and_single(self):
        """U-BW-01: width = high-low+1; single bit -> 1."""
        assert bit_slice_width(31, 16) == 16
        assert bit_slice_width(7, 7) == 1

    @pytest.mark.parametrize("hi,lo", [(None, None), (5, None), (None, 2)])
    def test_any_none_is_none(self, hi, lo):
        """U-BW-02: any None operand -> None."""
        assert bit_slice_width(hi, lo) is None

    def test_low_zero_is_falsy_not_none(self):
        """U-BW-03: low==0 is falsy but not None -> width 1, not None."""
        assert bit_slice_width(0, 0) == 1


# ===========================================================================
# parse_conn_ref (utils.py:91) -- 2 branches
# ===========================================================================

class TestParseConnRef:
    def test_instance_port_no_slice(self):
        """U-CR-01: instance.port, no slice."""
        assert parse_conn_ref("u_ss_cpu.sys_hclk") == ("u_ss_cpu", "sys_hclk", None, None)

    def test_bare_signal_instance_none(self):
        """U-CR-02: bare signal -> instance None."""
        assert parse_conn_ref("sys_sysresetn") == (None, "sys_sysresetn", None, None)

    def test_bare_with_slice(self):
        """U-CR-03: bare signal + slice."""
        assert parse_conn_ref("p1_in[6:0]") == (None, "p1_in", 6, 0)

    def test_instance_port_with_slice(self):
        """U-CR-04: instance.port + slice."""
        assert parse_conn_ref("u_ss.P1_OUT_MUX[6:4]") == ("u_ss", "P1_OUT_MUX", 6, 4)

    def test_split_first_dot_only(self):
        """U-CR-05: split on the FIRST dot only (port keeps the rest)."""
        assert parse_conn_ref("a.b.c") == ("a", "b.c", None, None)

    def test_slice_stripped_before_dot_split(self):
        """U-CR-06: slice stripped before the instance/port split."""
        assert parse_conn_ref("u.p[5]") == ("u", "p", 5, 5)

    def test_empty_and_leading_dot_edges(self):
        """U-CR-07: empty string -> bare empty port; leading dot -> empty inst."""
        assert parse_conn_ref("") == (None, "", None, None)
        assert parse_conn_ref(".x") == ("", "x", None, None)


# ===========================================================================
# get_param_value (utils.py:111) / flatten_params (utils.py:122)
# ===========================================================================

class TestGetParamValue:
    def test_flat_value(self):
        """U-GP-01: flat value returned directly."""
        assert get_param_value({"A": 5}, "A") == 5

    def test_nested_default_incl_zero(self):
        """U-GP-02: nested ``{default}`` -> the default (incl falsy 0)."""
        assert get_param_value({"D": {"default": 0}}, "D") == 0

    def test_dict_without_default_returns_whole(self):
        """U-GP-03: dict lacking 'default' -> the whole dict returned."""
        assert get_param_value({"C": {"nope": 1}}, "C") == {"nope": 1}

    def test_missing_is_none(self):
        """U-GP-04: missing key -> None."""
        assert get_param_value({}, "X") is None


class TestFlattenParams:
    def test_mixes_flat_and_nested(self):
        """U-FP-01: flattens nested ``{default}`` and keeps flat scalars."""
        out = flatten_params({"A": 5, "B": {"type": "int", "default": 7}})
        assert out == {"A": 5, "B": 7}

    def test_dict_without_default_kept_whole(self):
        """U-FP-02: dict without 'default' kept whole."""
        assert flatten_params({"C": {"nope": 1}}) == {"C": {"nope": 1}}

    def test_nested_falsy_default_zero(self):
        """U-FP-03: nested falsy default 0 preserved."""
        assert flatten_params({"D": {"default": 0}}) == {"D": 0}

    def test_does_not_mutate_input(self):
        """U-FP-04: returns a new dict; input unchanged."""
        src = {"A": 5, "B": {"type": "int", "default": 7}}
        snapshot = {"A": 5, "B": {"type": "int", "default": 7}}
        out = flatten_params(src)
        assert src == snapshot
        assert out is not src


# ===========================================================================
# write_if_changed (utils.py:156) -- needs tmp_path
# ===========================================================================

class TestWriteIfChanged:
    def test_absent_file_writes_true(self, tmp_path):
        """U-WC-01: absent file -> writes, returns True, exact content."""
        p = tmp_path / "x.sv"
        assert write_if_changed(p, "module a;") is True
        assert p.read_text() == "module a;"

    def test_identical_no_rewrite_false(self, tmp_path):
        """U-WC-02: identical content -> returns False (no rewrite)."""
        p = tmp_path / "x.sv"
        write_if_changed(p, "module a;")
        assert write_if_changed(p, "module a;") is False

    def test_only_generated_line_differs_false(self, tmp_path):
        """U-WC-03: ONLY a ``// Generated:`` timestamp line differs -> False,
        bytes on disk unchanged (old timestamp preserved)."""
        p = tmp_path / "x.sv"
        write_if_changed(p, "// Generated: 2020-01-01 00:00:00\nbody")
        assert write_if_changed(p, "// Generated: 2099-12-31 23:59:59\nbody") is False
        assert "2020-01-01" in p.read_text()
        assert "2099" not in p.read_text()

    def test_only_copyright_line_differs_false(self, tmp_path):
        """U-WC-04: ONLY a ``// Copyright YYYY,`` line differs -> False."""
        p = tmp_path / "x.sv"
        write_if_changed(p, "// Copyright 2024, SoC Labs\nbody")
        assert write_if_changed(p, "// Copyright 2026, SoC Labs\nbody") is False
        assert "2024" in p.read_text()

    def test_real_body_change_true(self, tmp_path):
        """U-WC-05: a real body change -> True, file rewritten."""
        p = tmp_path / "x.sv"
        write_if_changed(p, "module a;")
        assert write_if_changed(p, "module b;") is True
        assert p.read_text() == "module b;"

    def test_real_change_while_timestamp_differs_true(self, tmp_path):
        """U-WC-06: body changes AND timestamp differs -> True (body wins)."""
        p = tmp_path / "x.sv"
        write_if_changed(p, "// Generated: 2020-01-01 00:00:00\nmodule a;")
        assert write_if_changed(p, "// Generated: 2099-12-31 23:59:59\nmodule b;") is True
        assert "module b;" in p.read_text()

    def test_parent_dirs_auto_created(self, tmp_path):
        """U-WC-07: parent directories are created."""
        p = tmp_path / "n" / "deep" / "x.flist"
        assert write_if_changed(p, "hi") is True
        assert p.exists()
        assert p.parent.is_dir()

    def test_accepts_str_and_path(self, tmp_path):
        """U-WC-08: accepts both a str path and a Path."""
        p_str = str(tmp_path / "s.txt")
        p_path = tmp_path / "p.txt"
        assert write_if_changed(p_str, "via str") is True
        assert write_if_changed(p_path, "via path") is True

    def test_trailing_newline_only_diff_false(self, tmp_path):
        """U-WC-09: FOOT-GUN. ``splitlines`` drops the trailing newline, so a
        trailing-newline-only diff is treated as identical -> False."""
        p = tmp_path / "x.txt"
        write_if_changed(p, "line")
        assert write_if_changed(p, "line\n") is False
        # File on disk is still the original (no trailing newline).
        assert p.read_text() == "line"

    def test_interior_blank_line_added_true(self, tmp_path):
        """U-WC-10: an added interior blank line is a real diff -> True."""
        p = tmp_path / "x.txt"
        write_if_changed(p, "a\nb\n")
        assert write_if_changed(p, "a\n\nb\n") is True

    def test_non_four_digit_copyright_year_not_stripped_true(self, tmp_path):
        """U-WC-11: a non-4-digit copyright year is NOT a timestamp line, so
        the change is real -> True."""
        p = tmp_path / "x.sv"
        write_if_changed(p, "// Copyright abcd, X\nbody")
        assert write_if_changed(p, "// Copyright efgh, X\nbody") is True

    def test_return_type_is_bool(self, tmp_path):
        """U-WC-12: return value is a bool."""
        p = tmp_path / "x.txt"
        assert isinstance(write_if_changed(p, "x"), bool)


# ===========================================================================
# _strip_timestamps (utils.py:148) + _TIMESTAMP_LINE_RE (utils.py:137)
# ===========================================================================

class TestStripTimestamps:
    @pytest.mark.parametrize("prefix", [
        "// Generated: 2026-06-09 12:34:56",
        " * Generated: 2026-06-09 12:34:56",
        "# Generated: 2026-06-09 12:34:56",
    ])
    def test_strips_generated_forms(self, prefix):
        """U-ST-01: strips ``//`` / ``*`` / ``#`` Generated forms; body kept."""
        out = _strip_timestamps(prefix + "\nmodule foo;")
        assert out == "module foo;"

    def test_strips_auto_generated_line(self):
        """U-ST-02: strips ``> Auto-generated ... on <ts> ...`` incl trailing."""
        out = _strip_timestamps("> Auto-generated x on 2026-06-09 12:34:56 y\nbody")
        assert out == "body"

    def test_strips_copyright_line(self):
        """U-ST-03: strips ``// Copyright YYYY, ...``."""
        assert _strip_timestamps("// Copyright 2026, SoC Labs\nbody") == "body"

    def test_normal_code_line_kept(self):
        """U-ST-04: an ordinary code line is kept."""
        assert _strip_timestamps("module foo;") == "module foo;"

    def test_generated_with_trailing_text_not_stripped(self):
        """U-ST-05: ASYMMETRY. The ``//`` Generated alternative is anchored
        with ``\\s*$`` so trailing text after the timestamp -> KEPT."""
        line = "// Generated: 2026-06-09 12:34:56 extra"
        assert _strip_timestamps(line) == line

    def test_block_comment_generated_not_stripped(self):
        """U-ST-06: FOOT-GUN (BI-13 root). A ``/* Generated: ... */`` block
        comment does NOT match any alternative -> KEPT."""
        line = "/* Generated: 2026-06-09 12:34:56 */"
        assert _strip_timestamps(line) == line

    def test_malformed_single_digit_date_not_stripped(self):
        """U-ST-07: single-digit date fields don't match ``\\d{4}-\\d{2}...`` -> KEPT."""
        line = "// Generated: 2026-6-9 1:2:3"
        assert _strip_timestamps(line) == line

    def test_output_drops_trailing_newline(self):
        """U-ST-08: ``splitlines`` + ``join`` drops the trailing newline."""
        assert _strip_timestamps("a\n") == "a"

    def test_pattern_is_compiled_regex(self):
        """U-ST-09: regression guard for helpers.py reuse -- the symbols exist
        and ``_TIMESTAMP_LINE_RE`` is a compiled pattern."""
        assert isinstance(_TIMESTAMP_LINE_RE, re.Pattern)
        assert _strip_timestamps is not None

    def test_strip_preserves_interior_body_lines(self):
        """U-ST-04 (extended): a generated line between two body lines is
        removed but both body lines survive in order."""
        src = "module a;\n// Generated: 2026-06-09 12:34:56\nendmodule"
        assert _strip_timestamps(src) == "module a;\nendmodule"
