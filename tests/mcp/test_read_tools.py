# Copyright 2026, SoC Labs (www.soclabs.org)
"""WP1 gate: MCP read tools over GuiService (in-process, no subprocess)."""
import asyncio
import contextlib
import io
import json
import re
import shutil
import sys
from pathlib import Path

import pytest
import yaml

pytest.importorskip("mcp.server.mcpserver")
from mcp.client._memory import InMemoryTransport  # noqa: E402
from mcp.client.client import Client  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures"
HELLO = ROOT / "docs" / "examples" / "hello_soc"

from soc_model.configurator.__main__ import main as check_main  # noqa: E402
from soc_model.gui_service import GuiService  # noqa: E402
from soc_model.mcp_server import server  # noqa: E402
from soc_model.backends.text import SoCTextBackend  # noqa: E402

pytestmark = pytest.mark.unit


def call(name, args=None, first=None):
    """Fresh session; optionally open a project first; return structured dict."""
    async def go():
        server._S.adapter = server._S.top = server._S.svc = None
        async with Client(InMemoryTransport(server.srv)) as c:
            if first:
                r = await c.call_tool("soc_open_project", first)
                assert json.loads(r.content[0].text)["ok"], r.content[0].text
            r = await c.call_tool(name, args or {})
            return json.loads(r.content[0].text)  # plain JSON dict
    return asyncio.run(go())


def open_args(root, top, libs):
    return {"path": str(root), "top_yaml": top, "lib_dirs": libs}


HELLO_OPEN = open_args(HELLO, "hello_soc.yaml", ["."])


def test_tool_list_and_names():
    async def go():
        async with Client(InMemoryTransport(server.srv)) as c:
            return [t.name for t in (await c.list_tools()).tools]
    names = set(asyncio.run(go()))
    assert {"soc_open_project", "soc_validate", "soc_memory_map",
            "soc_irq_map", "soc_catalog", "soc_describe",
            "soc_free_resources"} <= names
    assert all(re.fullmatch(r"[A-Za-z0-9_-]+", n) for n in names)


def test_open_project_hello():
    r = call("soc_open_project", HELLO_OPEN)
    assert r["ok"] and r["top_yaml"] == "hello_soc.yaml"


def test_open_unknown_project_is_structured_error():
    r = call("soc_open_project", {"path": "/nonexistent/zzz"})
    assert r["ok"] is False and r["error"]["code"] == "project_not_found"


def test_tool_without_project_is_structured_error():
    r = call("soc_validate")
    assert r["ok"] is False and r["error"]["code"] == "no_project"


def test_open_missing_top_is_structured_error():
    r = call("soc_open_project", open_args(HELLO, "nope.yaml", ["."]))
    assert r["ok"] is False and r["error"]["code"] == "file_not_found"


def test_top_override_outside_project_refused():
    r = call("soc_validate", {"top": "/etc/passwd"}, first=HELLO_OPEN)
    assert r["ok"] is False and r["error"]["code"] == "path_outside_project"


def _cli_check(yaml_path, libs):
    argv = ["check", str(yaml_path)]
    for lb in libs:
        argv += ["--lib-dir", str(lb)]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = check_main(argv)
    return rc, buf.getvalue()


@pytest.mark.parametrize("fx", sorted(p.name for p in FIX.glob("*_soc.yaml")))
def test_validate_matches_configurator_check(fx):
    rc, out = _cli_check(FIX / fx, [FIX])
    r = call("soc_validate", first=open_args(FIX, fx, ["."]))
    assert r["ok"] == (rc == 0)
    assert f"{r['errors']} error(s), {r['warnings']} warning(s)" in out
    for m in r["messages"]:
        assert m["message"] in out


def test_validate_hello_clean():
    r = call("soc_validate", first=HELLO_OPEN)
    assert r["ok"] and r["errors"] == 0


def test_memory_map_matches_text_backend(tmp_path):
    r = call("soc_memory_map", first=HELLO_OPEN)
    assert r["ok"]
    svc = GuiService(HELLO, [str(HELLO)])
    module, _ = svc.build_model(yaml.safe_load((HELLO / "hello_soc.yaml").read_text()))
    out = tmp_path / "memmap.txt"
    try:
        SoCTextBackend().__class__  # class exists
        be = SoCTextBackend()
        be.top = module
        be.generate_memory_map(str(out))
        text = out.read_text()
    finally:
        out.unlink(missing_ok=True)
    rows = re.findall(r"^\s+(\S+)\s+\S*\s+0x([0-9A-F]{8})\s+0x([0-9A-F]{8})",
                      text, re.M)
    got = [(t["name"], t["base"], t["end"])
           for ic in r["interconnects"] for t in ic["targets"]]
    assert got == [(n, int(b, 16), int(e, 16)) for n, b, e in rows] and got


def test_overlapping_targets_not_ok(tmp_path):
    d = tmp_path / "ov"
    shutil.copytree(HELLO, d)
    data = yaml.safe_load((d / "hello_soc.yaml").read_text())
    ic = data["module"]["interconnects"][0]
    ic["targets"].append(dict(ic["targets"][0], name="sram_1", base=0x800))
    (d / "hello_soc.yaml").write_text(yaml.dump(data, sort_keys=False))
    r = call("soc_memory_map", first=open_args(d, "hello_soc.yaml", ["."]))
    assert r["ok"] is False
    assert {o["target"] for o in r["overlaps"]} == {"sram_0", "sram_1"}
    v = call("soc_validate", first=open_args(d, "hello_soc.yaml", ["."]))
    assert v["ok"] is False and v["errors"] >= 1
    assert any("overlap" in m["message"].lower() for m in v["messages"])


def test_describe_hello():
    r = call("soc_describe", first=HELLO_OPEN)
    assert r["name"] == "hello_soc"
    assert [i["name"] for i in r["instances"]] == ["u_sram_0"]
    assert r["interconnects"][0]["targets"][0]["name"] == "sram_0"
    assert r["params"]["SYS_ADDR_W"] == 32


def test_free_resources_hello():
    r = call("soc_free_resources", first=HELLO_OPEN)
    assert r["ok"]
    slot = r["address_slots"][0]
    assert slot["next_free_base"] == 0x1000 and slot["gaps"] == []


def test_free_resources_gap(tmp_path):
    d = tmp_path / "gap"
    shutil.copytree(HELLO, d)
    data = yaml.safe_load((d / "hello_soc.yaml").read_text())
    data["module"]["interconnects"][0]["targets"][0]["base"] = 0x2000
    (d / "hello_soc.yaml").write_text(yaml.dump(data, sort_keys=False))
    r = call("soc_free_resources", first=open_args(d, "hello_soc.yaml", ["."]))
    assert r["address_slots"][0]["gaps"][0]["size"] == 0x2000


def test_irq_map_and_catalog():
    r = call("soc_irq_map", first=HELLO_OPEN)
    assert r["ok"] and r["count"] == 0
    c = call("soc_catalog", first=HELLO_OPEN)
    assert c["ok"] and "tiny_region" in c["modules"]


def test_yaml_resource_and_path_guard():
    async def go():
        server._S.adapter = server._S.top = server._S.svc = None
        async with Client(InMemoryTransport(server.srv)) as c:
            await c.call_tool("soc_open_project", HELLO_OPEN)
            ok = await c.read_resource("soc://yaml/hello_soc.yaml")
            with pytest.raises(Exception):
                await c.read_resource("soc://yaml/..%2F..%2F..%2Fpyproject.toml")
            return ok
    res = asyncio.run(go())
    assert "module:" in res.contents[0].text
