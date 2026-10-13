# Copyright 2026, SoC Labs (www.soclabs.org)
"""soc_glue_mux2 selects at BUILD time, and the generator refuses anything else.

soc_glue_mux2 used to be a runtime 2:1 mux whose `sel` port was tied to a
parameter. Elaboration folded it, but the source read as a mux in a reset path
(HAL GLTASR) with a 32-bit parameter on a 1-bit port (HAL SIZMIS). The cell now
picks its leg with `parameter SEL` and a generate (eth rc4 taped out with this
form, d04de2e). That only works when the select is something elaboration can
evaluate, so the generator now checks it:

  * a literal 0/1 (0, 1, 1'b0, 1'b1, '1, 32'd0 ...) renders as written;
  * a parameter the module declares ($NAME or NAME) renders as NAME[0], the bit
    the old 1-bit port kept, so the netlist does not change;
  * anything else -- a net or port (a RUNTIME select), an undeclared $NAME, an
    expression, x/z, a literal other than 0 or 1 -- is refused, by the backend
    and, before anything is written, by the CLI.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from soc_model.backends import toplevel as tl
from soc_model.model import GlueLogicEntry, Instance, Interface, Module, Param
from tests.helpers import arm_free_env

pytestmark = pytest.mark.unit

PKG_ROOT = Path(__file__).resolve().parents[2]
MUX2_SV = PKG_ROOT / "rtl" / "soc_glue" / "soc_glue_mux2.sv"


def _wire(name, width=1):
    return Interface(name=name, type="wire", direction="internal", params={"WIDTH": width})


def _top(select, name="mux_soc"):
    top = Module(name=name, gen=True)
    top.params = {"CLK_RST_CONSUMER": Param(name="CLK_RST_CONSUMER", type="int", default=0)}
    top.interfaces = [Interface(name="clk", type="wire", direction="in", params={"WIDTH": 1})]
    top.internal_wires = [_wire("a"), _wire("b"), _wire("y"), _wire("sel_net")]
    top.glue_logic = [GlueLogicEntry(name="mx", type="mux2", output="y",
                                     inputs=["a", "b"], select=select, desc="mux")]
    return top


def _select_error():
    """The refusal type (an AttributeError on a generator without the check)."""
    return getattr(tl, "Mux2SelectError")


def _render(top, tmp_path):
    out = tl.SoCTopLevelBackend(top).generate(tmp_path / "rtl", tmp_path / "flist")
    return out.read_text()


# --- the cell -------------------------------------------------------------------
def test_cell_selects_with_a_parameter_not_a_port():
    src = re.sub(r"//[^\n]*", "", MUX2_SV.read_text())
    assert re.search(r"\bparameter\s+SEL\b", src), "soc_glue_mux2 has no parameter SEL"
    assert not re.search(r"\binput\s+wire\s+sel\b", src), "soc_glue_mux2 still has a sel port"
    assert "generate" in src and "SEL != 0" in src


# --- what renders ---------------------------------------------------------------
@pytest.mark.parametrize("select", ["$CLK_RST_CONSUMER", "CLK_RST_CONSUMER"])
def test_parameter_select_renders_bit0_on_SEL(tmp_path, select):
    sv = _render(_top(select), tmp_path)
    assert ".SEL  (CLK_RST_CONSUMER[0])" in sv
    assert ".sel (" not in sv


@pytest.mark.parametrize("select", ["0", "1", "1'b0", "1'b1", "'0", "'1", "32'd1", "1'h0", "4'b0001"])
def test_literal_select_renders_as_written(tmp_path, select):
    sv = _render(_top(select), tmp_path)
    assert f".SEL  ({select})" in sv
    assert ".sel (" not in sv


def test_missing_select_is_in0(tmp_path):
    """No select: SEL 1'b0 (in0), what the port form defaulted to."""
    sv = _render(_top(None), tmp_path)
    assert ".SEL  (1'b0)" in sv


# --- what is refused ------------------------------------------------------------
@pytest.mark.parametrize("select, why", [
    ("sel_net", "not a build-time constant"),             # a net: a runtime mux
    ("u_core.SEL_O", "not a build-time constant"),        # an instance port
    ("$CLK_RST_CONSUMER & sel_net", "not a build-time constant"),
    ("~CLK_RST_CONSUMER", "not a build-time constant"),   # an expression
    ("$NOT_DECLARED", "does not declare"),                # a parameter the module lacks
    ("1'bx", "not a build-time constant"),
    ("2", "must be 0 or 1"),                              # the old port kept bit 0 of it
    ("2'b10", "must be 0 or 1"),
])
def test_runtime_select_refused(tmp_path, select, why):
    err = _select_error()
    with pytest.raises(err, match=why) as e:
        tl.mux2_select(_top(select).glue_logic[0], _top(select))
    assert "runtime-mux glue type" in str(e.value)
    with pytest.raises(err, match=why):
        _render(_top(select), tmp_path)


def test_check_walks_rendered_subsystems():
    """check_mux2_selects() covers every module the generator renders: a
    runtime select inside a gen:True subsystem is named with that module."""
    sub = _top("sel_net", name="eth_like_ss")
    sub.instances = [Instance(instance_name="u_leaf", module_name="leaf")]   # rendered: gen + instances
    top = Module(name="parent_soc", gen=True)
    top.instances = [Instance(instance_name="u_sub", module_name="eth_like_ss",
                              resolved_module=sub)]
    errors = tl.check_mux2_selects(top)
    assert len(errors) == 1 and errors[0].startswith("eth_like_ss: glue 'mx' (mux2)")
    sub.glue_logic[0].select = "$CLK_RST_CONSUMER"
    assert tl.check_mux2_selects(top) == []


def test_cli_refuses_a_runtime_select_before_writing(fixtures_dir, tmp_path):
    src = (fixtures_dir / "minimal_soc.yaml").read_text()
    anchor = "  interconnects:\n"
    glue = ("  internal_wires:\n"
            "    - { name: mx_a, type: wire, params: { WIDTH: 1 } }\n"
            "    - { name: mx_b, type: wire, params: { WIDTH: 1 } }\n"
            "    - { name: mx_y, type: wire, params: { WIDTH: 1 } }\n"
            "    - { name: mx_s, type: wire, params: { WIDTH: 1 } }\n"
            "  glue_logic:\n"
            "    - { name: mx, type: mux2, inputs: [mx_a, mx_b], select: mx_s, output: mx_y }\n")
    assert src.count(anchor) == 1
    for f in fixtures_dir.iterdir():
        if f.is_dir():
            shutil.copytree(f, tmp_path / f.name)
    (tmp_path / "minimal_soc.yaml").write_text(src.replace(anchor, glue + anchor))
    build = tmp_path / "build_soc"
    env = arm_free_env()
    env.update(PYTHONPATH=str(PKG_ROOT), PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, "-m", "soc_model", str(tmp_path / "minimal_soc.yaml"),
                        "--build-dir", str(build), "--lib-dir", str(tmp_path)],
                       cwd=str(PKG_ROOT), env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, universal_newlines=True, timeout=300)
    assert r.returncode == 1, r.stdout[-2000:] + r.stderr[-2000:]
    assert "minimal_soc: glue 'mx' (mux2): select 'mx_s' is not a build-time constant" in r.stderr
    assert "Nothing was generated." in r.stderr
    assert not build.exists() or not any(p.is_file() for p in build.rglob("*"))


# --- the select's VALUE, per instance (review R-TRAIN6) -------------------------------
def _hier(sub_default, inst_value=None):
    """parent_soc -> u_eth (eth_like_ss, gen, mux2 on $CLK_RST_CONSUMER)."""
    sub = _top("$CLK_RST_CONSUMER", name="eth_like_ss")
    sub.params["CLK_RST_CONSUMER"] = Param(name="CLK_RST_CONSUMER", type="int", default=sub_default)
    sub.instances = [Instance(instance_name="u_leaf", module_name="leaf")]
    top = Module(name="parent_soc", gen=True)
    top.params = {"CONSUMER": Param(name="CONSUMER", type="int", default=0)}
    params = {} if inst_value is None else {"CLK_RST_CONSUMER": inst_value}
    top.instances = [Instance(instance_name="u_eth", module_name="eth_like_ss",
                              params=params, resolved_module=sub)]
    return top


@pytest.mark.parametrize("sub_default, inst_value", [(0, None), (1, None), (0, 1), (1, 0), (0, "$CONSUMER")])
def test_select_values_0_or_1_pass(sub_default, inst_value):
    assert tl.check_mux2_selects(_hier(sub_default, inst_value)) == []


@pytest.mark.parametrize("sub_default, inst_value, where", [
    (0, 2, "is 2 at parent_soc/u_eth"),             # the instance's override
    (2, None, "is 2 at eth_like_ss (its default)"),  # the RTL default
    (0, -1, "is -1 at parent_soc/u_eth"),
    (0, "'x'", "at parent_soc/u_eth"),               # not a number
])
def test_select_value_other_than_0_or_1_refused(sub_default, inst_value, where):
    errors = tl.check_mux2_selects(_hier(sub_default, inst_value))
    assert errors and any(where in e and "must be 0 or 1" in e for e in errors), errors


def test_select_value_through_a_config_override():
    """--config-override reaches the select: CONSUMER=2 at the top -> refused."""
    top = _hier(0, "$CONSUMER")
    assert tl.check_mux2_selects(top, {"CONSUMER": 1}) == []
    errors = tl.check_mux2_selects(top, {"CONSUMER": 2})
    assert any("is 2 at parent_soc/u_eth" in e for e in errors), errors
