# Copyright 2026, SoC Labs (www.soclabs.org)
"""WP4 gate: MCP edit tools with dry-run drafts (in-process, no subprocess)."""
import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytest.importorskip("mcp.server.mcpserver")
from mcp.client._memory import InMemoryTransport  # noqa: E402
from mcp.client.client import Client  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402

from soc_model.configurator.project import load_project  # noqa: E402
from soc_model.gui_service import GuiService  # noqa: E402
from soc_model.mcp_server import edit_tools  # noqa: E402

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
HELLO = ROOT / "docs" / "examples" / "hello_soc"
FIX = ROOT / "tests" / "fixtures"


class Session:
    adapter = top = svc = None


@pytest.fixture
def proj(tmp_path):
    """Copy of hello_soc plus the sram_block component package (RAM_ADDR_W
    knob, floor 10) so hard limits apply to tiny_region instances."""
    dst = tmp_path / "proj"
    shutil.copytree(HELLO, dst)
    (dst / "components").mkdir()
    shutil.copy(FIX / "components" / "sram.yaml", dst / "components")
    return dst


def make(proj):
    S = Session()
    ad = load_project(proj)
    ad.top_yaml, ad.lib_dirs = "hello_soc.yaml", ["."]
    S.adapter, S.top = ad, ad.top_yaml
    S.svc = GuiService(base_dir=str(proj), lib_dirs=ad.abs_lib_dirs,
                       rules_file=ad.abs_rules)
    srv = MCPServer("t")
    edit_tools.register(srv, S)
    return srv


def call(srv, name, args=None):
    async def go():
        async with Client(InMemoryTransport(srv)) as c:
            r = await c.call_tool(name, args or {})
            return json.loads(r.content[0].text)
    return asyncio.run(go())


def test_tool_names():
    async def go(s):
        async with Client(InMemoryTransport(s)) as c:
            return {t.name for t in (await c.list_tools()).tools}
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        dst = Path(d) / "p"
        shutil.copytree(HELLO, dst)
        names = asyncio.run(go(make(dst)))
    assert {"soc_add_component", "soc_remove_component",
            "soc_update_component", "soc_set_irq_bit", "soc_set_param",
            "soc_save"} <= names


def test_dry_run_add_second_instance_draft_only(proj):
    srv = make(proj)
    top = proj / "hello_soc.yaml"
    before = top.read_bytes()
    r = call(srv, "soc_add_component",
             {"module_name": "tiny_region", "instance_name": "u_sram_1",
              "size": 0x1000})
    assert r["ok"] and r["dry_run"] is True
    assert top.read_bytes() == before            # top byte-identical
    draft = proj / ".configurator" / "drafts" / (r["draft_id"] + ".yaml")
    assert draft.is_file()
    assert r["validate"]["ok"], r["validate"]["messages"]
    # diff touches only the expected keys
    assert r["changed"]
    for p in r["changed"]:
        assert p.startswith(("module.instances[", "module.interconnects[")), p
    assert any("u_sram_1" in p for p in r["changed"])
    assert "+" in r["diff"] and "u_sram_1" in r["diff"]
    assert not [p for p in r["changed"] if p.startswith("module.params")]
    # generator accepts the draft (text + toplevel backends)
    out = proj / "build"
    env = dict(os.environ)
    env.pop("ARM_IP_LIBRARY_PATH", None)
    p = subprocess.run(
        [sys.executable, "-m", "soc_model", str(draft), "--lib-dir",
         str(proj), "--build-dir", str(out), "--only", "toplevel,text"],
        cwd=ROOT, env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout[-600:] + p.stderr[-600:]


def test_save_promotes_draft_and_real_write(proj):
    srv = make(proj)
    r = call(srv, "soc_add_component",
             {"module_name": "tiny_region", "instance_name": "u_sram_1"})
    s = call(srv, "soc_save", {"draft_id": r["draft_id"]})
    assert s["ok"]
    data = yaml.safe_load((proj / "hello_soc.yaml").read_text())
    names = [i["instance_name"] for i in data["module"]["instances"]]
    assert names == ["u_sram_0", "u_sram_1"]
    assert not (proj / ".configurator/drafts" / (r["draft_id"] + ".yaml")).exists()
    # dry_run=False writes the top directly
    r2 = call(srv, "soc_remove_component",
              {"instance_name": "u_sram_1", "dry_run": False})
    assert r2["ok"] and "draft_id" not in r2
    data = yaml.safe_load((proj / "hello_soc.yaml").read_text())
    assert [i["instance_name"] for i in data["module"]["instances"]] == ["u_sram_0"]


def test_chained_edit_on_draft(proj):
    srv = make(proj)
    a = call(srv, "soc_add_component",
             {"module_name": "tiny_region", "instance_name": "u_sram_1"})
    b = call(srv, "soc_remove_component",
             {"instance_name": "u_sram_0", "draft": a["draft_id"]})
    assert b["ok"]
    assert all("u_sram_1" not in p for p in b["changed"])  # vs. draft a
    assert any("u_sram_0" in p for p in b["changed"])


def test_plant_below_knob_floor_raises_not_clamps(proj):
    srv = make(proj)
    top = proj / "hello_soc.yaml"
    before = top.read_bytes()
    r = call(srv, "soc_add_component",
             {"module_name": "tiny_region", "instance_name": "u_sram_1",
              "params": {"RAM_ADDR_W": 1}})
    assert r["ok"] is False and r["error"]["code"] == "rejected"
    assert "RAM_ADDR_W" in r["error"]["message"]
    assert not (proj / ".configurator" / "drafts").exists() or \
        not list((proj / ".configurator" / "drafts").iterdir())
    assert top.read_bytes() == before
    # update path and set_param path enforce it too
    r = call(srv, "soc_update_component",
             {"instance_name": "u_sram_0", "params": {"RAM_ADDR_W": 1}})
    assert r["ok"] is False
    r = call(srv, "soc_set_param",
             {"name": "RAM_ADDR_W", "value": 1, "instance": "u_sram_0"})
    assert r["ok"] is False and r["error"]["code"] == "rejected"


def test_set_param_module_and_instance(proj):
    srv = make(proj)
    r = call(srv, "soc_set_param", {"name": "SRAM_RAM_ADDR_W", "value": 13})
    assert r["ok"] and r["changed"] == ["module.params.SRAM_RAM_ADDR_W.default"]
    r = call(srv, "soc_set_param", {"name": "NOPE", "value": 1})
    assert r["ok"] is False


def test_unknown_module_and_bad_draft(proj):
    srv = make(proj)
    assert call(srv, "soc_add_component",
                {"module_name": "no_such_ip"})["error"]["code"] == "rejected"
    assert call(srv, "soc_save", {"draft_id": "zzzzzzzz"})["ok"] is False
    assert call(srv, "soc_save", {"draft_id": "../../x"})["ok"] is False
    r = call(srv, "soc_remove_component",
             {"instance_name": "u_sram_0", "draft": "../../etc"})
    assert r["ok"] is False


def test_save_refuses_invalid_draft(proj):
    srv = make(proj)
    r = call(srv, "soc_remove_component", {"instance_name": "u_sram_0"})
    assert r["ok"]
    top = proj / "hello_soc.yaml"
    before = top.read_bytes()
    assert not r["validate"]["ok"], "removing the only memory must fail validation"
    s = call(srv, "soc_save", {"draft_id": r["draft_id"]})
    assert s["ok"] is False and s["error"]["code"] == "validation_failed"
    assert top.read_bytes() == before
    assert call(srv, "soc_save",
                {"draft_id": r["draft_id"], "force": True})["ok"]
