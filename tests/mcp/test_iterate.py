# Copyright 2026, SoC Labs (www.soclabs.org)
"""WP5 gate: soc_iterate composite loop (in-process; make/sim are shell stubs,
the validate/edit stages use the real GuiService)."""
import asyncio
import json
import shutil
from pathlib import Path

import pytest
import yaml

pytest.importorskip("mcp.server.mcpserver")
from mcp.client._memory import InMemoryTransport  # noqa: E402
from mcp.client.client import Client  # noqa: E402

from soc_model.mcp_server import server  # noqa: E402

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
HELLO = ROOT / "docs" / "examples" / "hello_soc"
FIX = ROOT / "tests" / "fixtures"
SIM_OK = "echo '$stop at time 99 ns'; echo '** TEST PASSED **' #"
SIM_BAD = "echo 'sim: FAILED -- boom'; exit 1 #"


def make_project(tmp_path, make):
    root = tmp_path / "proj"
    shutil.copytree(HELLO, root)
    (root / "components").mkdir()
    shutil.copy(FIX / "components" / "sram.yaml", root / "components")
    m = {"soc": "touch generated.flag #", "lint": "echo lint ok #"}
    m.update(make)
    (root / "configurator.yaml").write_text(json.dumps({
        "top_yaml": "hello_soc.yaml", "lib_dirs": ["."],
        "build_dir": "build_soc", "make": m}))
    return root


def iterate(root, args=None):
    async def go():
        server._S.adapter = server._S.top = server._S.svc = None
        async with Client(InMemoryTransport(server.srv)) as c:
            r = await c.call_tool("soc_open_project", {"path": str(root)})
            assert json.loads(r.content[0].text)["ok"]
            r = await c.call_tool("soc_iterate", args or {})
            return json.loads(r.content[0].text)
    return asyncio.run(go())


def names(out):
    return {s["name"]: s["status"] for s in out["stages"]}


def test_registered_with_all_wp_tools():
    async def go():
        async with Client(InMemoryTransport(server.srv)) as c:
            return {t.name for t in (await c.list_tools()).tools}
    t = asyncio.run(go())
    assert {"soc_iterate", "soc_add_component", "sim_debug",
            "sim_run", "soc_validate"} <= t


def test_no_edits_passes(tmp_path):
    root = make_project(tmp_path, {"sim": SIM_OK})
    out = iterate(root)
    assert out["ok"] and out["verdict"] == "PASS", out
    assert names(out) == {"edit": "skipped", "validate": "ok",
                          "generate": "ok", "lint": "skipped",
                          "test": "skipped", "sim": "ok", "debug": "skipped"}
    assert (root / "generated.flag").exists()


def test_overlap_edit_stops_at_validate_and_names_overlap(tmp_path):
    """Plant: base=0 collides with u_sram_0. Must stop at validate, never
    generate, and leave the top YAML byte-identical."""
    root = make_project(tmp_path, {"sim": SIM_OK})
    top = root / "hello_soc.yaml"
    before = top.read_bytes()
    out = iterate(root, {"edits": [{
        "tool": "add_component", "module_name": "tiny_region",
        "instance_name": "u_sram_1", "base": 0}]})
    assert not out["ok"] and out["verdict"] == "FAIL"
    ff = out["first_failure"]
    assert ff["stage"] == "validate", out
    assert ff["finding"]["code"] == "address_overlap"
    assert "sram_0" in ff["finding"]["message"]   # the pre-existing window is named
    assert names(out).get("generate") is None          # never ran
    assert not (root / "generated.flag").exists()
    assert top.read_bytes() == before


def test_good_edit_is_applied_then_loop_runs(tmp_path):
    root = make_project(tmp_path, {"sim": SIM_OK})
    out = iterate(root, {"edits": [{
        "tool": "soc_add_component", "module_name": "tiny_region",
        "instance_name": "u_sram_1"}]})
    assert out["ok"], out
    assert names(out)["edit"] == "ok" and names(out)["generate"] == "ok"
    data = yaml.safe_load((root / "hello_soc.yaml").read_text())
    assert [i["instance_name"] for i in data["module"]["instances"]] == [
        "u_sram_0", "u_sram_1"]


def test_apply_false_leaves_top_untouched_and_skips_rest(tmp_path):
    root = make_project(tmp_path, {"sim": SIM_OK})
    before = (root / "hello_soc.yaml").read_bytes()
    out = iterate(root, {"apply": False, "edits": [{
        "tool": "add_component", "module_name": "tiny_region",
        "instance_name": "u_sram_1"}]})
    assert (root / "hello_soc.yaml").read_bytes() == before
    assert out["ok"] and out["draft_id"]
    assert names(out)["generate"] == "skipped" and names(out)["sim"] == "skipped"


def test_rejected_edit_stops_at_edit(tmp_path):
    root = make_project(tmp_path, {"sim": SIM_OK})
    out = iterate(root, {"edits": [{"tool": "remove_component",
                                    "instance_name": "nope"}]})
    assert out["first_failure"]["stage"] == "edit"
    assert "validate" not in names(out)


def test_sim_fail_is_first_failure_and_debug_runs(tmp_path):
    root = make_project(tmp_path, {"sim": SIM_BAD})
    sd = root / "simulate/sim/hello/verilator"
    sd.mkdir(parents=True)
    out = iterate(root)
    assert not out["ok"]
    assert out["first_failure"]["stage"] == "sim"
    assert names(out)["debug"] in ("ok", "fail")        # ran, not skipped
    assert "debug" in out["first_failure"]


def test_stop_on_never_keeps_going(tmp_path):
    root = make_project(tmp_path, {"sim": SIM_OK, "lint": "exit 1 #"})
    out = iterate(root, {"lint": True, "stop_on": "never"})
    assert names(out)["lint"] == "fail" and names(out)["sim"] == "ok"
    assert out["first_failure"]["stage"] == "lint"
    out = iterate(root, {"lint": True})
    assert names(out)["sim"] == "skipped"


def test_stub_banner_without_rc_zero_never_passes(tmp_path):
    root = make_project(tmp_path, {
        "sim": "echo '** TEST PASSED **'; echo 'timed out'; exit 1 #"})
    assert iterate(root)["ok"] is False


@pytest.mark.parametrize("args", [{"simulator": "vcs"}, {"stop_on": "x"},
                                  {"edits": [{"tool": "rm_rf"}]}])
def test_bad_input(tmp_path, args):
    root = make_project(tmp_path, {"sim": SIM_OK})
    out = iterate(root, args)
    assert out["ok"] is False and out["error"]["code"] == "bad_input"


def test_no_project():
    async def go():
        server._S.adapter = server._S.top = server._S.svc = None
        async with Client(InMemoryTransport(server.srv)) as c:
            r = await c.call_tool("soc_iterate", {})
            return json.loads(r.content[0].text)
    assert asyncio.run(go())["error"]["code"] == "no_project"



def test_validation_failure_reports_only_the_error(tmp_path, monkeypatch):
    """55 warnings plus one error: the finding is that error, small."""
    from soc_model.gui_service import GuiService
    msgs = [{"level": "warning", "message": "w%d" % i} for i in range(55)]
    msgs.append({"level": "error", "message": "window outside address space"})
    monkeypatch.setattr(GuiService, "validate", lambda self, d: {
        "ok": False, "errors": 1, "warnings": 55, "messages": msgs})
    root = make_project(tmp_path, {"sim": SIM_OK})
    out = iterate(root)
    ff = out["first_failure"]
    assert ff["stage"] == "validate"
    assert ff["finding"]["message"] == "window outside address space"
    assert len(json.dumps(ff)) < 600
    assert "generate" not in names(out)
