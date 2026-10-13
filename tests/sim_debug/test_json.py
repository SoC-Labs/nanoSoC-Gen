# Copyright 2026, SoC Labs (www.soclabs.org)
"""sim_debug.analyze / Report.to_dict / --json / MCP tool sim_debug."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

PASS_LOG = (
    "[ADP] SoCLabs NanoSoC bootloader v1\n"
    "[ADP] REMAP done\n"
    "[ADP] hello\n"
    "[ADP] ** TEST PASSED **\n"
    "[ADP] Test Ended\n"
)
GOOD_TARMAC = "100 ns IT 00000100 2000 MOVS r0,#0\n200 ns IT 00000102 2001 MOVS r0,#1\n"
FAULT_TARMAC = (
    "100 ns IT 00000100 2000 MOVS r0,#0\n"
    "110 ns IT 00000102 6008 STR r0,[r1,#0]\n"
    "120 ns MW4_D 00000010 00000001\n"
    "130 ns E HARDFAULT_REQ\n"
)
FAULT_LOG = "[ADP] SoCLabs NanoSoC bootloader v1\n[ADP] REMAP done\n"


def make_sim(tmp_path, log, tarmac, name="hello"):
    d = tmp_path / name
    (d / "logs").mkdir(parents=True)
    (d / "logs" / "run_x.log").write_text(log)
    (d / "logs" / "tarmac0.log").write_text(tarmac)
    return d


def run_cli(*args):
    return subprocess.run([sys.executable, "-m", "sim_debug", *args], cwd=ROOT,
                          capture_output=True, text=True)


def test_pass_with_banner_and_remap(tmp_path):
    from sim_debug import analyze
    d = analyze(str(make_sim(tmp_path, PASS_LOG, GOOD_TARMAC))).to_dict()
    assert d["verdict"] == "PASS"
    assert d["outcome"]["banner"].startswith("SoCLabs NanoSoC")
    assert "REMAP" in d["outcome"]["remap"]
    assert d["outcome"]["test_result"] == "PASSED"
    assert d["counts"]["FATAL"] == 0 and d["counts"]["ERROR"] == 0
    assert any(f["title"] == "** TEST PASSED **" for f in d["findings"])
    json.dumps(d)  # serialisable


def test_plant_missing_pass_marker_is_fail(tmp_path):
    from sim_debug import analyze
    log = PASS_LOG.replace("[ADP] ** TEST PASSED **\n", "")
    d = analyze(str(make_sim(tmp_path, log, GOOD_TARMAC))).to_dict()
    assert d["verdict"] == "FAIL"
    assert "TEST PASSED" in d["reason"]


def test_no_sim_log_is_fail(tmp_path):
    from sim_debug import analyze
    sd = tmp_path / "empty"
    sd.mkdir()
    assert analyze(str(sd)).to_dict()["verdict"] == "FAIL"


def test_hardfault_fatal_with_pc(tmp_path):
    from sim_debug import analyze
    d = analyze(str(make_sim(tmp_path, FAULT_LOG, FAULT_TARMAC))).to_dict()
    assert d["verdict"] == "FAIL"
    fatal = [f for f in d["findings"] if f["severity"] == "FATAL"]
    assert len(fatal) == 1
    f = fatal[0]
    assert f["exception"] == "HARDFAULT_REQ"
    assert f["pc"] == "0x00000102"
    assert f["time_ns"] == 130
    assert f["mem_addr"] == "0x00000010" and f["mem_write"] is True
    assert "cause" in f   # write into the read-only boot ROM
    assert f["context"] and f["context"][-1]["type"] in ("IT", "MW")


def test_cli_json_and_exit_code(tmp_path):
    sd = make_sim(tmp_path, FAULT_LOG, FAULT_TARMAC)
    r = run_cli(str(sd), "--json")
    assert r.returncode == 1
    assert json.loads(r.stdout)["verdict"] == "FAIL"
    sd2 = make_sim(tmp_path, PASS_LOG, GOOD_TARMAC, name="ok")
    r2 = run_cli(str(sd2), "--json")
    assert r2.returncode == 0 and json.loads(r2.stdout)["verdict"] == "PASS"


def test_cli_text_is_render_of_analyze(tmp_path):
    from sim_debug import analyze
    sd = make_sim(tmp_path, PASS_LOG, GOOD_TARMAC)
    r = run_cli(str(sd), "--no-color")
    assert r.stdout == analyze(str(sd), use_colour=False).render() + "\n"
    assert "RESULT: PASS" in r.stdout and "--- Findings ---" in r.stdout


def test_mcp_tool(tmp_path):
    from mcp.client.client import Client
    from mcp.client._memory import InMemoryTransport
    from mcp.server.mcpserver import MCPServer
    from soc_model.mcp_server.sim_debug_tool import register

    srv = MCPServer("t")
    register(srv)
    good = make_sim(tmp_path, PASS_LOG, GOOD_TARMAC)
    bad = make_sim(tmp_path, FAULT_LOG, FAULT_TARMAC, name="bad")

    async def go():
        async with Client(InMemoryTransport(srv)) as c:
            names = [t.name for t in (await c.list_tools()).tools]
            r1 = await c.call_tool("sim_debug", {"sim_dir": str(good)})
            r2 = await c.call_tool("sim_debug", {"sim_dir": str(bad)})
            r3 = await c.call_tool("sim_debug", {"sim_dir": str(tmp_path / "nope")})
            return names, [json.loads(r.content[0].text) for r in (r1, r2, r3)]

    names, (g, b, n) = asyncio.run(go())
    assert "sim_debug" in names
    assert g["ok"] and g["verdict"] == "PASS"
    assert b["ok"] and b["verdict"] == "FAIL"
    assert n["ok"] is False and n["error"]["code"] == "no_sim_dir"
