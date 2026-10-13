# Copyright 2026, SoC Labs (www.soclabs.org)
"""Cross-instance wire check after the render (Train 7 G9).

`conn: u_x.PORT` makes the generator declare `wire W; // cross-instance wire:
u_x.PORT` and bind it on u_x. The check reads the render back: u_x must bind
.PORT(W) (the GPIO0 class: an output bound to another net; UNBOUND: no driver),
u_x's module must declare PORT (a typo or an undeclared port), and W must be
as wide as PORT and as every port that reads it (a YAML width that drifted from
the RTL). The prototype was ~/SoCLabs/archive/gpio0-fix-2026-10-02/xref_check.py.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from soc_model.backends.xref_check import check_rtl
from tests.helpers import build_module_from_yaml, arm_free_env

pytestmark = pytest.mark.unit

PKG_ROOT = Path(__file__).resolve().parents[2]

SRC = "module xsrc (input wire clk, output reg [{w}:0] dout);\nalways @(posedge clk) dout <= dout + 1;\nendmodule\n"
DST = "module xdst (input wire [{w}:0] din, output wire [3:0] q);\nassign q = din[3:0];\nendmodule\n"


def _soc(fixtures_dir, tmp_path, ref="u_src.dout", src_w=3, dst_w=3):
    d = yaml.safe_load((fixtures_dir / "minimal_soc.yaml").read_text())
    m = d["module"]
    m["interfaces"].append({"name": "led", "type": "wire", "direction": "out", "params": {"WIDTH": 4}})
    w = lambda n, dr: {"name": n, "type": "wire", "direction": dr, "params": {"WIDTH": 4}}
    m["instances"] += [
        {"instance_name": "u_src", "rtl_module": "xsrc",
         "interfaces": [dict(w("clk", "in"), params={"WIDTH": 1}), w("dout", "out")],
         "connections": [{"port": "clk", "conn": "sys_hclk"}]},
        {"instance_name": "u_dst", "rtl_module": "xdst",
         "interfaces": [w("din", "in"), w("q", "out")],
         "connections": [{"port": "din", "conn": ref}, {"port": "q", "conn": "led"}]},
    ]
    y = tmp_path / "y" / "minimal_soc.yaml"
    y.parent.mkdir()
    y.write_text(yaml.safe_dump(d, sort_keys=False))
    (tmp_path / "xsrc.v").write_text(SRC.format(w=src_w))
    (tmp_path / "xdst.v").write_text(DST.format(w=dst_w))
    fl = tmp_path / "x.flist"
    fl.write_text(f"{tmp_path / 'xsrc.v'}\n{tmp_path / 'xdst.v'}\n")
    return y, fl


def _cli(fixtures_dir, tmp_path, y, fl, *extra):
    env = arm_free_env()
    env.update(PYTHONPATH=str(PKG_ROOT), PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, "-m", "soc_model", str(y), "--lib-dir", str(fixtures_dir),
                        "--rtl-flist", str(fl), "--build-dir", str(tmp_path / "build"), *extra],
                       cwd=str(PKG_ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True, timeout=300)
    return r, r.stdout + r.stderr


def test_a_clean_reference_passes(fixtures_dir, tmp_path):
    r, out = _cli(fixtures_dir, tmp_path, *_soc(fixtures_dir, tmp_path))
    assert r.returncode == 0, out[-3000:]
    assert ("1 cross-instance wire(s) in 1 module(s) (1 expected from the model), 2 width(s) "
            "compared, 0 not compared: 0 finding(s)") in out


def test_producer_width_differs(fixtures_dir, tmp_path):
    r, out = _cli(fixtures_dir, tmp_path, *_soc(fixtures_dir, tmp_path, src_w=7))
    assert r.returncode == 1
    assert "u_src_dout (u_src.dout): the wire is 4 bit(s), xsrc.dout is 8" in out


def test_consumer_width_differs(fixtures_dir, tmp_path):
    r, out = _cli(fixtures_dir, tmp_path, *_soc(fixtures_dir, tmp_path, dst_w=7))
    assert r.returncode == 1
    assert "u_dst.din (xdst) is 8 bit(s), it is given u_src_dout (4 bit(s))" in out


def test_a_misspelt_port_fails_even_when_its_validation_error_is_waived(fixtures_dir, tmp_path):
    y, fl = _soc(fixtures_dir, tmp_path, ref="u_src.dot")
    r, out = _cli(fixtures_dir, tmp_path, y, fl,
                  "--validation-waive", "connection:u_dst.din=plant: the render-time check must catch it")
    assert r.returncode == 1, out[-3000:]
    assert "u_src_dot (u_src.dot): xsrc (xsrc.v) declares no port dot" in out


TOP = """module top (input wire clk);
    wire [3:0] u_a_dout;  // cross-instance wire: u_a.dout
    xsrc u_a (
        .clk(clk),
        .dout({bind})
    );
    xdst u_b (
        .din(u_a_dout),
        .q()
    );
{extra}endmodule
"""


@pytest.mark.parametrize("bind, extra, want", [
    ("u_a_dout", "", None),
    ("led_q", "", "u_a binds .dout(led_q), not u_a_dout"),          # the GPIO0 class
    (None, "", "UNBOUND: u_a does not bind .dout"),
    ("u_a_dout", "    wire u_z_y;  // cross-instance wire: u_z.y\n", "u_z is not an instance of top"),
])
def test_rendered_rtl_forms(tmp_path, bind, extra, want):
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    top = TOP.format(bind=bind or "", extra=extra)
    if bind is None:
        top = top.replace(",\n        .dout()", "")
    (rtl / "top.sv").write_text(top)
    (rtl / "xsrc.v").write_text(SRC.format(w=3))
    (rtl / "xdst.v").write_text(DST.format(w=3))
    rep = check_rtl(rtl)
    if want is None:
        assert rep.findings == [] and rep.wires == 1 and rep.widths_compared == 2
    else:
        assert any(want in f for f in rep.findings), rep.findings


# --- review R-TRAIN7 LOW-2/3: a render that hides its wires cannot pass -------------
def _rendered(fixtures_dir, tmp_path, **kw):
    y, fl = _soc(fixtures_dir, tmp_path, **kw)
    r, out = _cli(fixtures_dir, tmp_path, y, fl)
    assert r.returncode == 0, out[-3000:]
    top = build_module_from_yaml(y.parent, y.name, [fixtures_dir])
    return tmp_path / "build" / "rtl", fl, top


def test_a_wire_without_its_comment_is_a_shortfall(fixtures_dir, tmp_path):
    rtl, fl, top = _rendered(fixtures_dir, tmp_path)
    sv = rtl / "minimal_soc.sv"
    t = sv.read_text()
    assert t.count("// cross-instance wire: u_src.dout") == 1
    sv.write_text(t.replace("// cross-instance wire: u_src.dout", "// (comment removed)"))
    rep = check_rtl(rtl, [fl], top)
    assert rep.wires == 0 and rep.expected == 1
    assert any("1 of the 1 cross-instance wire(s) the model declares have no" in f
               and "u_src_dout" in f for f in rep.findings), rep.findings


def test_an_unparsed_module_fails_unless_waived(fixtures_dir, tmp_path):
    rtl, fl, top = _rendered(fixtures_dir, tmp_path)
    sv = rtl / "minimal_soc.sv"
    # an unbalanced port list: the module cannot be parsed, its wires go unchecked
    sv.write_text(sv.read_text().replace("module minimal_soc", "module minimal_soc (((", 1))
    rep = check_rtl(rtl, [fl], top)
    assert any("module minimal_soc declares 1 cross-instance wire(s) but could not be parsed" in f
               for f in rep.findings), rep.findings
    rep = check_rtl(rtl, [fl], top, {"minimal_soc": "plant"})
    assert not [f for f in rep.findings if "could not be parsed" in f]
    assert any("[waived: plant]" in n for n in rep.notes)
    rep = check_rtl(rtl, [fl], top, {"nosuch": "stale"})
    assert any("--xref-unparsed-waive nosuch: no unparsed module" in f for f in rep.findings)


def test_widths_not_compared_are_counted(fixtures_dir, tmp_path):
    rtl, fl, top = _rendered(fixtures_dir, tmp_path)
    (tmp_path / "only_src.flist").write_text(f"{tmp_path / 'xsrc.v'}\n")
    rep = check_rtl(rtl, [tmp_path / "only_src.flist"], top)
    assert rep.widths_compared == 1 and rep.not_compared == 1 and rep.findings == []
    assert "1 width(s) compared, 1 not compared: 0 finding(s)" in rep.summary()
