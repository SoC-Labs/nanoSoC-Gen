# Copyright 2026, SoC Labs (www.soclabs.org)
"""Tests for the HSEL-reader lint (soc_model/backends/hsel_lint.py).

Rule: every net a bus matrix drives from a ``<tgt>_hsel`` port must have a
reader outside the matrix. A leaf peripheral's HSEL input counts; a port that
is declared and never used does not. Each test below PLANTS a defect (or its
absence) and checks the rule's verdict, so the rule is shown to fail before a
pass is believed.
"""

import sys
from pathlib import Path

import pytest

from soc_model.backends.hsel_lint import (
    HselReaderLint, check_rtl, check_rtl_dir, index_rtl, rtl_files, main as lint_main)
from soc_model.backends.subsystem import SoCSubsystemBackend
from soc_model.backends.toplevel import SoCTopLevelBackend

from tests.helpers import build_module_from_yaml

pytestmark = pytest.mark.unit

HDR = "// AUTO-GENERATED FILE - DO NOT EDIT DIRECTLY.\n// Template:  soc_toplevel.sv.j2\n"


def _write(tmp_path, name, text, structural=True):
    p = tmp_path / f"{name}.sv"
    p.write_text((HDR if structural else "") + text)
    return p


def _top(extra_decl="", extra_body="", child="leaf_ip", child_hsel_port="HSEL",
         hsel_conn="t_hsel", htrans_conn="t_htrans"):
    """A structural top whose matrix target `t` drives instance u_t."""
    return f"""
module top_soc (
    input  wire clk{extra_decl}
);
    wire       t_hsel;
    wire [1:0] t_htrans;
    wire [1:0] t_htrans_g;
    {child} u_t (
        .{child_hsel_port}  ({hsel_conn}),
        .HTRANS ({htrans_conn})
    );
{extra_body}
    top_interconnect u_interconnect (
        .sys_hclk (clk),
        .t_hsel   (t_hsel),
        .t_htrans (t_htrans)
    );
endmodule
"""


# ---------------------------------------------------------------------------
# Synthetic plants
# ---------------------------------------------------------------------------
def test_leaf_outside_render_counts_as_reader(tmp_path):
    p = _write(tmp_path, "top_soc", _top())
    assert check_rtl([p]) == []


def test_planted_unused_hsel_fires(tmp_path):
    # The ETH_SS shape: the matrix drives t_hsel and nothing else touches it.
    p = _write(tmp_path, "top_soc", _top(hsel_conn="1'b1"))
    f = check_rtl([p])
    assert len(f) == 1
    assert f[0].net == "t_hsel" and f[0].matrix_port == "u_interconnect.t_hsel"
    assert "no reader outside the matrix" in str(f[0])


def test_child_port_declared_and_unused_fires(tmp_path):
    # The cpu_ss shape before 036d381: hsel reaches a generated child whose
    # port is declared and never read.
    top = _write(tmp_path, "top_soc", _top(child="child_ss", child_hsel_port="s_hsel"))
    child = _write(tmp_path, "child_ss", """
module child_ss (
    input  wire       s_hsel,
    input  wire [1:0] HTRANS
);
    ss_interconnect u_interconnect (.s_htrans (HTRANS));
endmodule
""")
    f = check_rtl([top, child])
    assert len(f) == 1
    assert "child_ss.s_hsel declared and not used" in str(f[0])


def test_child_port_read_in_child_is_a_reader(tmp_path):
    top = _write(tmp_path, "top_soc", _top(child="child_ss", child_hsel_port="s_hsel"))
    child = _write(tmp_path, "child_ss", """
module child_ss (
    input  wire       s_hsel,
    input  wire [1:0] HTRANS
);
    wire [1:0] g;
    assign g = s_hsel ? HTRANS : 2'b00;
    ss_interconnect u_interconnect (.s_htrans (g));
endmodule
""")
    assert check_rtl([top, child]) == []


def test_gate_in_parent_is_a_reader(tmp_path):
    # The fix for the initiator-boundary form: the parent reads hsel.
    p = _write(tmp_path, "top_soc", _top(
        hsel_conn="1'b1", htrans_conn="t_htrans_g",
        extra_body="    assign t_htrans_g = t_hsel ? t_htrans : 2'b00;"))
    assert check_rtl([p]) == []


def test_leaf_source_in_render_with_unused_hsel_fires(tmp_path):
    top = _write(tmp_path, "top_soc", _top(child="leaf_ip"))
    leaf = _write(tmp_path, "leaf_ip", """
module leaf_ip (input wire HSEL, input wire [1:0] HTRANS, output wire busy);
    assign busy = HTRANS[1];
endmodule
""", structural=False)
    f = check_rtl([top, leaf])
    assert len(f) == 1 and "leaf_ip.HSEL declared and not used" in str(f[0])


def test_leaf_source_in_render_reading_hsel_passes(tmp_path):
    top = _write(tmp_path, "top_soc", _top(child="leaf_ip"))
    leaf = _write(tmp_path, "leaf_ip", """
module leaf_ip (input wire HSEL, input wire [1:0] HTRANS, output wire busy);
    assign busy = HSEL & HTRANS[1];
endmodule
""", structural=False)
    assert check_rtl([top, leaf]) == []


def test_plain_copy_is_followed(tmp_path):
    # `assign x = t_hsel;` is not a read by itself: x must be read.
    p = _write(tmp_path, "top_soc", _top(
        hsel_conn="1'b1", extra_body="    wire x;\n    assign x = t_hsel;"))
    f = check_rtl([p])
    assert len(f) == 1 and "copied to x" in str(f[0])


def test_export_from_root_counts_as_reader(tmp_path):
    p = _write(tmp_path, "top_soc", _top(
        hsel_conn="1'b1", extra_decl=",\n    output wire exp_hsel",
        extra_body="    assign exp_hsel = t_hsel;"))
    assert check_rtl([p]) == []


def test_open_hsel_with_connected_htrans_fires(tmp_path):
    p = _write(tmp_path, "top_soc", """
module top_soc (input wire clk);
    wire [1:0] t_htrans;
    leaf_ip u_t (.HSEL (1'b1), .HTRANS (t_htrans));
    top_interconnect u_interconnect (.t_hsel ( ), .t_htrans (t_htrans));
endmodule
""")
    f = check_rtl([p])
    assert len(f) == 1 and "hsel left open" in str(f[0])


def test_generate_blocks_and_comments(tmp_path):
    # A conditional instance still counts; a mention in a comment does not.
    p = _write(tmp_path, "top_soc", """
module top_soc #(parameter EN = 1) (input wire clk);
    wire t_hsel;  // t_hsel is read below?  (comment only)
    wire u_hsel;
    /* t_hsel ? no */
    generate if (EN) begin : gen_u
    leaf_ip u_u (.HSEL (u_hsel));
    end endgenerate
    top_interconnect u_interconnect (.t_hsel (t_hsel), .u_hsel (u_hsel));
endmodule
""")
    f = check_rtl([p])
    assert [x.net for x in f] == ["t_hsel"]


def test_cli_exit_codes(tmp_path, capsys):
    bad = tmp_path / "bad"
    bad.mkdir()
    _write(bad, "top_soc", _top(hsel_conn="1'b1"))
    assert lint_main([str(bad)]) == 1
    assert "1 finding(s)" in capsys.readouterr().out
    good = tmp_path / "good"
    good.mkdir()
    _write(good, "top_soc", _top())
    assert lint_main([str(good), "-v"]) == 0
    assert "ok: top_soc.t_hsel <- top_soc.u_t.HSEL" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Review round 1 (f1c435b): reads the lint missed, and misses it must keep
# catching. Every test in this block except the last group failed on f1c435b.
# ---------------------------------------------------------------------------
def _leaf_top():
    return _top(child="periph", child_hsel_port="HSEL")


def _periph(body, ports="input wire HCLK, input wire HRESETn, input wire HSEL, "
                        "input wire [1:0] HTRANS, input wire HREADY"):
    return f"module periph ({ports});\n{body}\nendmodule\n"


PROCEDURAL_READS = {
    # the nanosoc_dbg_ahb_bridge pattern: the address phase is taken in an if
    "if_in_begin_end": """
  reg act;
  always @(posedge HCLK) begin
    act <= 1'b0;
    if (HSEL & HREADY & HTRANS[1]) act <= 1'b1;
  end""",
    "else_if": """
  reg act;
  always @(posedge HCLK or negedge HRESETn)
    if (!HRESETn) act <= 1'b0;
    else if (HSEL) act <= HTRANS[1];""",
    "nested_case_then_if": """
  reg [1:0] st;
  always @(posedge HCLK) begin
    case (st)
      2'd0: begin
        if (HSEL & HTRANS[1]) st <= 2'd1;
      end
      default: st <= 2'd0;
    endcase
  end""",
}


@pytest.mark.parametrize("name", sorted(PROCEDURAL_READS))
def test_in_render_leaf_reading_hsel_in_a_condition(name, tmp_path):
    top = _write(tmp_path, "top_soc", _leaf_top())
    leaf = _write(tmp_path, "periph", _periph(PROCEDURAL_READS[name]), structural=False)
    assert check_rtl([top, leaf]) == []


def test_structural_always_comb_if_is_a_read(tmp_path):
    p = _write(tmp_path, "top_soc", _top(
        hsel_conn="1'b1", htrans_conn="t_htrans_g",
        extra_body="    logic [1:0] r;\n    always_comb begin\n      r = 2'b00;\n"
                   "      if (t_hsel) r = t_htrans;\n    end\n"
                   "    assign t_htrans_g = r;"))
    assert check_rtl([p]) == []


@pytest.mark.parametrize("comment", [
    '    // a 3.5" panel window\n',                 # odd quote in a // comment
    '    // see addr/* for the range\n',            # /* inside a // comment
    '    /* a " quote in a block comment */\n',
])
def test_comment_cannot_hide_the_gate(comment, tmp_path):
    body = (comment + "    assign t_htrans_g = t_hsel ? t_htrans : 2'b00;\n"
            "    // a 7\" panel\n    /* end */")
    p = _write(tmp_path, "top_soc", _top(hsel_conn="1'b1", htrans_conn="t_htrans_g",
                                         extra_body=body))
    assert check_rtl([p]) == []


def test_string_cannot_fake_a_read(tmp_path):
    # the string is removed: "HSEL" inside $display is not a use
    top = _write(tmp_path, "top_soc", _leaf_top())
    leaf = _write(tmp_path, "periph", _periph('  initial $display("HSEL // /* ");'),
                  structural=False)
    f = check_rtl([top, leaf])
    assert len(f) == 1 and "periph.HSEL declared and not used" in str(f[0])


@pytest.mark.parametrize("case", ["dotstar_unknown", "struct_member", "interface_member"])
def test_unanalysable_is_a_note_not_a_finding(case, tmp_path):
    files = [_write(tmp_path, "top_soc", {
        "dotstar_unknown": _leaf_top(),
        "struct_member": _top(hsel_conn="1'b1", extra_body=(
            "    typedef struct packed { logic hsel; logic [1:0] htrans; } ahb_t;\n"
            "    ahb_t s;\n    assign s.hsel = t_hsel;")),
        "interface_member": _top(hsel_conn="1'b1", extra_body=(
            "    ahb_if bus_if (clk);\n    assign bus_if.hsel = t_hsel;")),
    }[case])]
    if case == "dotstar_unknown":
        files.append(_write(tmp_path, "periph", _periph("  core u_core (.*);"),
                            structural=False))
    lint = HselReaderLint(index_rtl(files))
    assert lint.run() == []
    assert len(lint.unanalysable) == 1
    assert "cannot be analysed" in str(lint.unanalysable[0])


def test_dotstar_resolved_when_the_source_is_present(tmp_path):
    top = _write(tmp_path, "top_soc", _leaf_top())
    leaf = _write(tmp_path, "periph", _periph("  core u_core (.*);"), structural=False)
    core_reads = _write(tmp_path, "core", "module core (input wire HSEL, output wire v);\n"
                        "  assign v = HSEL;\nendmodule\n", structural=False)
    assert check_rtl([top, leaf, core_reads]) == []
    core_ignores = _write(tmp_path, "core", "module core (input wire HSEL, output wire v);\n"
                          "  assign v = 1'b0;\nendmodule\n", structural=False)
    assert len(check_rtl([top, leaf, core_ignores])) == 1


# Misses the rule catches, and must keep catching.
def test_still_caught_two_levels_down(tmp_path):
    top = _write(tmp_path, "top_soc", _top(child="mid_ss", child_hsel_port="s_hsel"))
    mid = _write(tmp_path, "mid_ss", "module mid_ss (input wire s_hsel, input wire [1:0] HTRANS);\n"
                 "  low_ss u_l (.x_hsel(s_hsel), .x_htrans(HTRANS));\nendmodule\n")
    low = _write(tmp_path, "low_ss", "module low_ss (input wire x_hsel, input wire [1:0] x_htrans);\n"
                 "  ss_interconnect u_interconnect (.p_htrans(x_htrans));\nendmodule\n")
    f = check_rtl([top, mid, low])
    # rule 1 on the top's net, and rule 2 on low_ss's own unread input
    assert [(x.module, x.net, x.kind) for x in f] == \
        [("top_soc", "t_hsel", "matrix"), ("low_ss", "x_hsel", "input")]
    assert "low_ss.x_hsel declared and not used" in str(f[0])


@pytest.mark.parametrize("via_copy", [False, True])
def test_still_caught_read_only_by_the_matrix(via_copy, tmp_path):
    fb = "c" if via_copy else "t_hsel"
    p = _write(tmp_path, "top_soc", f"""
module top_soc (input wire clk);
    wire t_hsel; wire [1:0] t_htrans; wire c;
    assign c = t_hsel;
    leaf_ip u_t (.HTRANS(t_htrans));
    top_interconnect u_interconnect (.t_hsel(t_hsel), .t_htrans(t_htrans), .m_hsel({fb}));
endmodule
""")
    # the wrapper source tells the rule m_hsel is a matrix INPUT
    mx = tmp_path / "top_interconnect.sv"
    mx.write_text("// Template:  ahb_interconnect.sv.j2\n"
                  "module top_interconnect (output wire t_hsel, output wire [1:0] t_htrans,\n"
                  "                         input wire m_hsel);\nendmodule\n")
    f = check_rtl([p, mx])
    assert len(f) == 1 and f[0].htrans_used is True
    assert "can take a transfer the matrix did not select" in str(f[0])


# ---------------------------------------------------------------------------
# Real renders: clean after the fix, fires on each planted regression
# ---------------------------------------------------------------------------
def _render(fixtures_dir, out, fixture):
    top = build_module_from_yaml(fixtures_dir, f"{fixture}.yaml")
    SoCTopLevelBackend(top).generate(out / "rtl", out / "flist")
    SoCSubsystemBackend.generate_all(top, out)
    return out / "rtl"


# Every top-level fixture, so a new fixture is covered without editing this
# list. firmware_multicore_soc is a firmware-map fixture, not valid hardware:
# its eth_ss_0 target names u_eth_ss_0, which exposes no slave port, so the
# target's hsel AND htrans are connected to nothing (its hreadyout is
# undriven). The finding is asserted rather than the fixture changed, because
# its firmware goldens are built around its current shape.
ALL_FIXTURES = sorted(p.stem for p in
                      (Path(__file__).resolve().parents[1] / "fixtures").glob("*.yaml"))
EXPECTED_FINDINGS = {
    "firmware_multicore_soc": [("firmware_multicore_soc", "eth_ss_0_hsel", False)],
}


def test_fixture_list_is_complete():
    assert {"firmware_multicore_soc", "cmsdk_mux_soc", "constraints_soc",
            "gpio_chip_soc", "child_link_soc"} <= set(ALL_FIXTURES)


@pytest.mark.parametrize("fixture", ALL_FIXTURES)
def test_fixture_renders(fixture, fixtures_dir, tmp_path):
    rtl = _render(fixtures_dir, tmp_path, fixture)
    lint = HselReaderLint(index_rtl(rtl_files(rtl)))
    f = lint.run()
    assert [(x.module, x.net, x.htrans_used) for x in f] == \
        EXPECTED_FINDINGS.get(fixture, [])
    # No notes either: nested_subsystem_soc and child_link_soc used to
    # declare an external dap_s target port and connect it to nothing (dap_m
    # had no `passthrough:`); the fixtures now wire dap_m to dap_s.
    assert lint.unanalysable == [] and lint.unused_inputs == []
    for x in f:   # a dangling target is not described as taking transfers
        assert "dangling" in str(x) and "can take a transfer" not in str(x)


def _plant(path, old, new):
    text = path.read_text()
    assert text.count(old) == 1, f"plant target not found once: {old!r}"
    path.write_text(text.replace(old, new))


def test_planted_missing_parent_gate_initiator_form_fires(fixtures_dir, tmp_path):
    rtl = _render(fixtures_dir, tmp_path, "child_link_soc")
    # Undo the parent gate on the initiator-boundary link: bind the raw
    # matrix HTRANS and drop the assign (the pre-fix ETH_SS render).
    top = rtl / "child_link_soc.sv"
    _plant(top, "(u_cpu_ss_0_ext_m_htrans)", "(cpu_ss_0_ext_htrans)")
    _plant(top, "    assign u_cpu_ss_0_ext_m_htrans = cpu_ss_0_ext_hsel ? "
                "cpu_ss_0_ext_htrans : 2'b00;", "")
    f = check_rtl_dir(rtl)
    assert [(x.module, x.net) for x in f] == [("child_link_soc", "cpu_ss_0_ext_hsel")]


def test_planted_missing_both_gates_target_form_fires(fixtures_dir, tmp_path):
    rtl = _render(fixtures_dir, tmp_path, "child_link_soc")
    top, child = rtl / "child_link_soc.sv", rtl / "tiny_cpu_ss.sv"
    # Parent gate removed: the child's own gate (036d381) still reads hsel.
    _plant(top, "    assign u_cpu_ss_0_sub_s_htrans = cpu_ss_0_slave_hsel ? "
                "cpu_ss_0_slave_htrans : 2'b00;",
           "    assign u_cpu_ss_0_sub_s_htrans = cpu_ss_0_slave_htrans;")
    assert check_rtl_dir(rtl) == []
    # Child gate removed too (the b945836-era wrapper): the port is declared
    # and unused, so nothing reads the matrix hsel.
    _plant(child, "sub_s_hsel ? sub_s_htrans : 2'b00", "sub_s_htrans")
    f = check_rtl_dir(rtl)
    assert [(x.module, x.net) for x in f] == [("child_link_soc", "cpu_ss_0_slave_hsel"),
                                              ("tiny_cpu_ss", "sub_s_hsel")]
    assert "tiny_cpu_ss.sub_s_hsel declared and not used" in str(f[0])


# ---------------------------------------------------------------------------
# Generation fails on an unread hsel (and --allow-unread-hsel only reports)
# ---------------------------------------------------------------------------
def _run_main(monkeypatch, argv):
    from soc_model.__main__ import main
    monkeypatch.setattr(sys, "argv", ["soc_model", *[str(a) for a in argv]])
    try:
        main()
    except SystemExit as exc:
        return 0 if exc.code is None else int(exc.code)
    return 0


def test_generation_passes_with_the_gate(fixtures_dir, tmp_path, monkeypatch, capsys):
    rc = _run_main(monkeypatch, [fixtures_dir / "child_link_soc.yaml",
                                 "--lib-dir", fixtures_dir, "--build-dir", tmp_path / "b"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "--- HSEL-reader check ---" in out
    assert ("5 matrix-driven hsel(s) and 1 hsel input(s) checked in 2 structural "
            "module(s): 0 finding(s), 0 note(s)") in out


def test_generation_fails_without_the_gate(fixtures_dir, tmp_path, monkeypatch, capsys):
    # Regress the parent backend to its pre-fix behaviour (no child-link gate).
    monkeypatch.setattr(SoCTopLevelBackend, "_child_link_htrans_gates", lambda self: [])
    rc = _run_main(monkeypatch, [fixtures_dir / "child_link_soc.yaml",
                                 "--lib-dir", fixtures_dir, "--build-dir", tmp_path / "b"])
    out, err = capsys.readouterr()
    assert rc == 1
    assert "cpu_ss_0_ext_hsel" in err and "no reader outside the matrix" in err
    # the message names the fix and the escape, and the summary counts
    assert "gated with this hsel" in err and "--allow-unread-hsel" in err
    assert ("5 matrix-driven hsel(s) and 1 hsel input(s) checked in 2 structural "
            "module(s): 1 finding(s)") in out
    # The target-boundary link is still covered by the child's own gate.
    assert "cpu_ss_0_slave_hsel (" not in err
    rc = _run_main(monkeypatch, [fixtures_dir / "child_link_soc.yaml",
                                 "--lib-dir", fixtures_dir, "--build-dir", tmp_path / "c",
                                 "--allow-unread-hsel"])
    assert rc == 0
    assert "WARNING: 1 bus-matrix hsel(s)" in capsys.readouterr().err
    # the explicit strict flag (the default today) wins when given last
    rc = _run_main(monkeypatch, [fixtures_dir / "child_link_soc.yaml",
                                 "--lib-dir", fixtures_dir, "--build-dir", tmp_path / "d",
                                 "--allow-unread-hsel", "--fail-on-unread-hsel"])
    assert rc == 1


# ---------------------------------------------------------------------------
# Rule 2: a structural module's INPUT hsel must be read when its htrans is
# used (the 036d381 form, and a top module's own target-boundary port).
# ---------------------------------------------------------------------------
def _child(body, hsel="s_hsel"):
    return (f"module child_ss (input wire {hsel}, input wire [1:0] s_htrans);\n"
            f"{body}\nendmodule\n")


def test_rule2_unread_input_with_used_htrans_fires(tmp_path):
    c = _write(tmp_path, "child_ss", _child("  ss_interconnect u_interconnect (.p_htrans(s_htrans));"))
    lint = HselReaderLint(index_rtl([c]))
    f = lint.run()
    assert [(x.module, x.net, x.kind, x.htrans_used) for x in f] == \
        [("child_ss", "s_hsel", "input", True)]
    assert "input s_hsel is never read" in str(f[0])
    assert "uses s_htrans without it" in str(f[0])


@pytest.mark.parametrize("body", [
    "  wire [1:0] g;\n  assign g = s_hsel ? s_htrans : 2'b00;\n"
    "  ss_interconnect u_interconnect (.p_htrans(g));",
    "  reg [1:0] g;\n  always @* if (s_hsel) g = s_htrans; else g = 2'b00;\n"
    "  ss_interconnect u_interconnect (.p_htrans(g));",
])
def test_rule2_read_input_passes(body, tmp_path):
    c = _write(tmp_path, "child_ss", _child(body))
    assert check_rtl([c]) == []


def test_rule2_comment_mention_is_not_a_read(tmp_path):
    c = _write(tmp_path, "child_ss", _child(
        "  // s_hsel ? s_htrans : 2'b00\n  ss_interconnect u_interconnect (.p_htrans(s_htrans));"))
    assert len(check_rtl([c])) == 1


def test_rule2_fully_unconnected_interface_is_a_note(tmp_path):
    c = _write(tmp_path, "child_ss", _child("  wire unrelated;"))
    lint = HselReaderLint(index_rtl([c]))
    assert lint.run() == []
    assert len(lint.unused_inputs) == 1
    assert "the interface is unconnected" in str(lint.unused_inputs[0])


def test_rule2_catches_a_pre_036d381_child(fixtures_dir, tmp_path, monkeypatch):
    # Child wrapper without its own gate. The parent still gates the link
    # (rule 1 passes), so only rule 2 can see the child's unread input.
    monkeypatch.setattr(SoCSubsystemBackend, "_hsel_gated_passthrough_initiators",
                        lambda self: {})
    rtl = _render(fixtures_dir, tmp_path, "child_link_soc")
    f = check_rtl_dir(rtl)
    assert [(x.module, x.net, x.kind) for x in f] == [("tiny_cpu_ss", "sub_s_hsel", "input")]


def test_rule2_catches_an_ungated_top_target_boundary_port(fixtures_dir, tmp_path,
                                                           monkeypatch):
    # passthrough_soc before its top-level ext_s gate: ext_s_hsel unread,
    # ext_s_htrans straight into the matrix.
    monkeypatch.setattr(SoCTopLevelBackend, "_hsel_gated_passthrough_initiators",
                        lambda self: {})
    rtl = _render(fixtures_dir, tmp_path, "passthrough_soc")
    f = check_rtl_dir(rtl)
    assert [(x.module, x.net, x.kind) for x in f] == [("passthrough_soc", "ext_s_hsel", "input")]
