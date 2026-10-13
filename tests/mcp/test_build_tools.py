# Copyright 2026, SoC Labs (www.soclabs.org)
"""WP2 gate: build/test/sim/lint MCP tools. `make` calls are replaced by
shell stubs in a temp project (the real JobRunner runs them) or by a mocked
_exec; no EDA tool is needed."""
import asyncio
import json
import shutil
import time
from pathlib import Path

import pytest

pytest.importorskip("mcp.server.mcpserver")
from mcp.client._memory import InMemoryTransport  # noqa: E402
from mcp.client.client import Client  # noqa: E402

from soc_model.mcp_server import build_tools, server  # noqa: E402

pytestmark = pytest.mark.unit
HELLO = Path(__file__).resolve().parents[2] / "docs" / "examples" / "hello_soc"

SIM_OK = r"""echo 'sim: building'; echo '$stop at time 1234 ns'; echo '** TEST PASSED **'"""
SIM_TIMEOUT_CMD = r"""echo '** TEST PASSED **'; for a in "$@"; do case $a in SIM_TIMEOUT=*) t=${a#SIM_TIMEOUT=};; esac; done; echo "sim: FAILED -- timed out after ${t:?}s"; exit 1"""


def script(tmp_path, name, body):
    """A stub `make` command: a script file, args appended by the tool."""
    p = tmp_path / name
    p.write_text("#!/bin/bash\n" + body + "\n")
    p.chmod(0o755)
    return str(p)


def make_project(tmp_path, make=None):
    root = tmp_path / "proj"
    shutil.copytree(HELLO, root)
    (root / "configurator.yaml").write_text(json.dumps({
        "top_yaml": "hello_soc.yaml", "lib_dirs": ["."],
        "build_dir": "build_soc", "make": make or {}}))
    return root


def call(root, name, args=None):
    async def go():
        server._S.adapter = server._S.top = server._S.svc = None
        async with Client(InMemoryTransport(server.srv)) as c:
            r = await c.call_tool("soc_open_project", {"path": str(root)})
            assert json.loads(r.content[0].text)["ok"]
            r = await c.call_tool(name, args or {})
            return json.loads(r.content[0].text)
    return asyncio.run(go())


def test_tools_registered():
    async def go():
        async with Client(InMemoryTransport(server.srv)) as c:
            return {t.name for t in (await c.list_tools()).tools}
    assert {"soc_generate", "test_list", "test_run", "sim_run",
            "lint_run"} <= asyncio.run(go())


def test_no_project_is_structured_error():
    async def go():
        server._S.adapter = server._S.top = server._S.svc = None
        async with Client(InMemoryTransport(server.srv)) as c:
            r = await c.call_tool("sim_run", {})
            return json.loads(r.content[0].text)
    out = asyncio.run(go())
    assert out["ok"] is False and out["error"]["code"] == "no_project"


def test_sim_pass(tmp_path):
    root = make_project(tmp_path, {"sim": script(tmp_path, "s.sh", SIM_OK)})
    out = call(root, "sim_run", {"testname": "hello"})
    assert out["verdict"] == "PASS" and out["ok"] is True
    assert out["stop_time"] == "1234 ns"
    assert len(out["console_tail"]) <= 20 and out["log"].endswith(
        "simulate/sim/hello/verilator/logs/run_hello.log")


def test_sim_timeout_never_passes(tmp_path):
    """Plant: SIM_TIMEOUT=5 reaches the command and the run must be FAIL even
    though the banner is printed."""
    root = make_project(tmp_path, {"sim": script(tmp_path, "s.sh", SIM_TIMEOUT_CMD)})
    out = call(root, "sim_run", {"testname": "hello", "timeout": 5})
    assert out["verdict"] == "FAIL" and out["ok"] is False
    assert out["rc"] == 1


def test_sim_rc0_without_banner_fails(tmp_path):
    root = make_project(tmp_path, {"sim": "echo hi #"})
    assert call(root, "sim_run")["verdict"] == "FAIL"


def test_sim_rc2_is_setup(tmp_path):
    root = make_project(tmp_path, {"sim": "echo 'sim: no verilator'; exit 2 #"})
    out = call(root, "sim_run")
    assert out["verdict"] == "SETUP" and out["ok"] is False


@pytest.mark.parametrize("bad", ["a b", "x;rm", "$(id)", "", "../x"])
def test_sim_rejects_bad_testname(tmp_path, bad):
    root = make_project(tmp_path, {"sim": "exit 0 #"})
    out = call(root, "sim_run", {"testname": bad})
    assert out["ok"] is False and out["error"]["code"] == "bad_input"


def test_sim_rejects_bad_timeout(tmp_path):
    root = make_project(tmp_path, {"sim": "exit 0 #"})
    assert call(root, "sim_run", {"timeout": 0})["error"]["code"] == "bad_input"


def test_sim_passes_args_to_make(tmp_path):
    root = make_project(tmp_path, {"sim": "echo ARGS:$@ #"})
    # base command echoes; extra words arrive after it
    seen = {}

    async def fake(stage, keep=400):
        seen["cmd"] = stage.cmd
        return 0, ["** TEST PASSED **", "$stop at 9 ns"], 0.1
    orig = build_tools._exec
    build_tools._exec = fake
    try:
        call(root, "sim_run", {"testname": "t1", "timeout": 7,
                               "accelerator": "yes", "accel": "acc_mac"})
    finally:
        build_tools._exec = orig
    assert "TESTNAME=t1" in seen["cmd"] and "SIM_TIMEOUT=7" in seen["cmd"]
    assert "ACCELERATOR=yes" in seen["cmd"] and "ACCEL=acc_mac" in seen["cmd"]


LIST_OUT = ("echo '  no-aaa      [guards] no tracked file names AAA'; "
            "echo '  unit-acc_mac  [unit] cocotb unit tests'; ")


def test_test_list(tmp_path):
    root = make_project(tmp_path, {"test": f"bash -c \"{LIST_OUT}\" #"})
    out = call(root, "test_list")
    assert out["ok"] and [i["name"] for i in out["items"]] == [
        "no-aaa", "unit-acc_mac"]
    assert out["items"][1]["group"] == "unit"


def test_test_list_failure_is_error(tmp_path):
    root = make_project(tmp_path, {"test": "exit 3 #"})
    assert call(root, "test_list")["error"]["code"] == "list_failed"


def _results_writer(root, rows, rc=0):
    async def fake(stage, keep=400):
        fake.cmd = stage.cmd
        d = Path(root) / "build/qv/test"
        d.mkdir(parents=True, exist_ok=True)
        (d / "results.json").write_text(json.dumps({"items": rows}))
        return rc, ["make test: done"], 0.5
    return fake


def test_test_run_returns_rows(tmp_path):
    root = make_project(tmp_path, {})
    rows = [{"name": "no-aaa", "status": "PASS", "detail": "ok",
             "seconds": 1.0, "log": "build/qv/test/no-aaa.log"},
            {"name": "unit-acc_mac", "status": "PASS", "detail": "7 tests",
             "seconds": 9.0, "log": "build/qv/test/unit-acc_mac.log"}]
    fake = _results_writer(root, rows)
    orig, build_tools._exec = build_tools._exec, fake
    try:
        out = call(root, "test_run", {"only": ["no-aaa", "unit-acc_mac"],
                                      "jobs": 2})
    finally:
        build_tools._exec = orig
    assert out["ok"] and out["counts"] == {"PASS": 2} and out["failed"] == []
    assert [r["name"] for r in out["items"]] == ["no-aaa", "unit-acc_mac"]
    assert "--only no-aaa,unit-acc_mac" in fake.cmd and "--jobs 2" in fake.cmd


def test_test_run_fail_row_not_ok(tmp_path):
    root = make_project(tmp_path, {})
    rows = [{"name": "lint", "status": "FAIL", "detail": "x"}]
    orig, build_tools._exec = build_tools._exec, _results_writer(root, rows, 1)
    try:
        out = call(root, "test_run", {"only": ["lint"]})
    finally:
        build_tools._exec = orig
    assert out["ok"] is False and out["failed"] == ["lint"]


def test_test_run_stale_results_json_rejected(tmp_path):
    """A results.json left by an earlier run must not be returned."""
    root = make_project(tmp_path, {"test": "exit 1 #"})
    d = root / "build/qv/test"
    d.mkdir(parents=True)
    f = d / "results.json"
    f.write_text(json.dumps({"items": [{"name": "x", "status": "PASS"}]}))
    old = time.time() - 3600
    import os
    os.utime(f, (old, old))
    out = call(root, "test_run", {"only": ["x"]})
    assert out["ok"] is False and out["error"]["code"] == "no_results"


@pytest.mark.parametrize("args", [{"only": ["a;b"]}, {"skip": ["$(x)"]},
                                  {"jobs": 0}, {"jobs": 99}])
def test_test_run_rejects_bad_input(tmp_path, args):
    root = make_project(tmp_path, {"test": "exit 0 #"})
    assert call(root, "test_run", args)["error"]["code"] == "bad_input"


def test_lint_run(tmp_path):
    ok = call(make_project(tmp_path / "a", {"lint": "echo clean #"}),
              "lint_run")
    bad = call(make_project(tmp_path / "b", {"lint": "echo %Error; exit 1 #"}),
               "lint_run")
    assert ok["verdict"] == "PASS" and bad["verdict"] == "FAIL"
    assert "%Error" in bad["console_tail"][-1]


def test_generate_reports_changed_files_and_freshness(tmp_path):
    root = make_project(tmp_path, {
        "soc": "mkdir -p build_soc/x && echo hi > build_soc/x/new.txt #"})
    out = call(root, "soc_generate")
    assert out["ok"] and out["files_changed"] == ["x/new.txt"]
    assert out["stages"][0]["status"] == "ok"
    assert out["freshness"]["state"] in ("fresh", "stale", "unknown")


def test_generate_failure_reports_tail(tmp_path):
    root = make_project(tmp_path, {"soc": "echo boom; exit 4 #"})
    out = call(root, "soc_generate")
    assert out["ok"] is False and out["stages"][0]["rc"] == 4
    assert out["tail"] == ["boom"]


def test_generate_only_uses_soc_model_cmd(tmp_path):
    root = make_project(tmp_path, {"soc": "exit 9 #"})
    seen = {}

    async def fake(stage, keep=400):
        seen["cmd"] = stage.cmd
        return 0, [], 0.0
    orig, build_tools._exec = build_tools._exec, fake
    try:
        out = call(root, "soc_generate", {"only": ["text", "flist"]})
    finally:
        build_tools._exec = orig
    assert out["ok"] and "-m soc_model" in seen["cmd"]
    assert "--only text,flist" in seen["cmd"]
    assert call(root, "soc_generate", {"only": ["x y"]})["ok"] is False


def test_generate_real_only_text(tmp_path):
    """No make stub: the default command really generates (text backend)."""
    root = make_project(tmp_path, {})
    out = call(root, "soc_generate", {"only": ["text"]})
    assert out["ok"], out
    assert out["files_changed"], "text backend wrote nothing"
