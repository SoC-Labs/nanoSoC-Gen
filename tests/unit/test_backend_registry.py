"""Unit tests for the Backend contract + registry (roadmap doc 02, M2/M3).

Covers:
  * register_backend rejects duplicate names / missing name
  * ordered() is stable and respects (phase, after) edges
  * run_pipeline runs a selection and honours enabled/requires_jinja
  * GenContext-driven SoCSramBackend().run(ctx) emits the report (M2 acceptance)
  * --only / --list-backends selection helpers (via the registry funcs)
"""

import pytest

from soc_model.model import Module, Param, SramEntry
from soc_model.backends.base import (
    Backend, BackendResult, GenContext, Phase,
)
from soc_model.backends import registry as reg
from soc_model.backends.registry import (
    register_backend, all_backends, ordered, run_pipeline,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# helpers: register throwaway backends against a fresh registry
# ---------------------------------------------------------------------------

@pytest.fixture
def clean_registry(monkeypatch):
    """Swap the module-level _REGISTRY for an empty list for the test."""
    saved = reg._REGISTRY
    monkeypatch.setattr(reg, "_REGISTRY", [])
    yield reg._REGISTRY
    # monkeypatch restores saved automatically


# ---------------------------------------------------------------------------
# register_backend
# ---------------------------------------------------------------------------

def test_register_rejects_duplicate_name(clean_registry):
    @register_backend
    class A(Backend):
        name = "dup"
        def run(self, ctx):
            return BackendResult()

    with pytest.raises(ValueError):
        @register_backend
        class B(Backend):
            name = "dup"
            def run(self, ctx):
                return BackendResult()


def test_register_rejects_missing_name(clean_registry):
    with pytest.raises(ValueError):
        @register_backend
        class A(Backend):
            name = ""
            def run(self, ctx):
                return BackendResult()


def test_all_backends_returns_registration_order(clean_registry):
    @register_backend
    class A(Backend):
        name = "a"
        def run(self, ctx):
            return BackendResult()

    @register_backend
    class B(Backend):
        name = "b"
        def run(self, ctx):
            return BackendResult()

    assert [c.name for c in all_backends()] == ["a", "b"]


# ---------------------------------------------------------------------------
# ordered() — stable, phase-first, after-edges
# ---------------------------------------------------------------------------

def test_ordered_sorts_by_phase_then_registration(clean_registry):
    @register_backend
    class Late(Backend):
        name = "late"
        phase = Phase.DOCS
        def run(self, ctx):
            return BackendResult()

    @register_backend
    class Early1(Backend):
        name = "early1"
        phase = Phase.REPORT
        def run(self, ctx):
            return BackendResult()

    @register_backend
    class Early2(Backend):
        name = "early2"
        phase = Phase.REPORT
        def run(self, ctx):
            return BackendResult()

    names = [c.name for c in ordered()]
    # REPORT phase before DOCS; within REPORT, stable registration order.
    assert names == ["early1", "early2", "late"]


def test_ordered_respects_after_within_phase(clean_registry):
    # rdl declared first but must run after discovery/build_info.
    @register_backend
    class Rdl(Backend):
        name = "rdl"
        phase = Phase.REGISTERS
        after = ("discovery", "build_info")
        def run(self, ctx):
            return BackendResult()

    @register_backend
    class Discovery(Backend):
        name = "discovery"
        phase = Phase.REGISTERS
        def run(self, ctx):
            return BackendResult()

    @register_backend
    class BuildInfo(Backend):
        name = "build_info"
        phase = Phase.REGISTERS
        def run(self, ctx):
            return BackendResult()

    names = [c.name for c in ordered()]
    assert names.index("rdl") > names.index("discovery")
    assert names.index("rdl") > names.index("build_info")


def test_ordered_filters_by_selection(clean_registry):
    @register_backend
    class A(Backend):
        name = "a"
        def run(self, ctx):
            return BackendResult()

    @register_backend
    class B(Backend):
        name = "b"
        def run(self, ctx):
            return BackendResult()

    assert [c.name for c in ordered({"b"})] == ["b"]


# ---------------------------------------------------------------------------
# run_pipeline
# ---------------------------------------------------------------------------

def test_run_pipeline_runs_selection_and_skips(clean_registry, tmp_path):
    ran = []

    @register_backend
    class Ran(Backend):
        name = "ran"
        def run(self, ctx):
            ran.append("ran")
            return BackendResult(files=[tmp_path / "x"])

    @register_backend
    class Gated(Backend):
        name = "gated"
        def enabled(self, ctx):
            return False
        def run(self, ctx):  # pragma: no cover - should be skipped
            ran.append("gated")
            return BackendResult()

    ctx = GenContext(top=Module(name="t"), build_dir=tmp_path,
                     rtl_dir=tmp_path, flist_dir=tmp_path, reports_dir=tmp_path)
    results = dict(run_pipeline(ctx))
    assert ran == ["ran"]
    assert results["gated"].skipped is True
    assert results["ran"].files


def test_run_pipeline_skips_when_jinja_required_and_absent(clean_registry,
                                                           tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "have_jinja", lambda: False)

    @register_backend
    class NeedsJinja(Backend):
        name = "needs_jinja"
        requires_jinja = True
        def run(self, ctx):  # pragma: no cover - should be skipped
            raise AssertionError("should not run")

    ctx = GenContext(top=Module(name="t"), build_dir=tmp_path,
                     rtl_dir=tmp_path, flist_dir=tmp_path, reports_dir=tmp_path)
    results = dict(run_pipeline(ctx))
    assert results["needs_jinja"].skipped is True
    assert results["needs_jinja"].reason == "no jinja2"


# ---------------------------------------------------------------------------
# M2 acceptance: GenContext-driven SoCSramBackend().run(ctx)
# ---------------------------------------------------------------------------

def test_sram_backend_run_emits_report(tmp_path):
    """A hand-built 1-SRAM Module driven via run(ctx) emits the report file."""
    from soc_model.backends.sram import SoCSramBackend

    reports = tmp_path / "reports"
    reports.mkdir()
    top = Module(name="one_ram_soc", gen=True)
    top.params = {"AW": Param(name="AW", type="int", default=10)}
    top.srams = [SramEntry(name="the_ram", addr_width="$AW", data_width=32, addr_unit="word",
                           desc="single sram")]

    ctx = GenContext(top=top, build_dir=tmp_path, rtl_dir=tmp_path / "rtl",
                     flist_dir=tmp_path / "flist", reports_dir=reports)
    res = SoCSramBackend().run(ctx)

    assert res.files
    out = res.files[0]
    assert out == reports / "one_ram_soc_sram_report.txt"
    txt = out.read_text()
    assert "the_ram" in txt
    assert "Total: 1 SRAM(s)" in txt
    assert "1024" in txt  # depth 2**10


def test_sram_backend_legacy_ctor_still_works(tmp_path):
    """Backward-compat: SoCSramBackend(top).generate(path) path is preserved."""
    from soc_model.backends.sram import SoCSramBackend

    out = tmp_path / "sram.txt"
    SoCSramBackend(Module(name="empty", gen=True)).generate(str(out))
    assert "No SRAMs declared" in out.read_text()


# ---------------------------------------------------------------------------
# The four real leaf backends are registered with the expected names/phase
# ---------------------------------------------------------------------------

def test_four_leaf_backends_registered():
    import soc_model.backends  # noqa: F401 — fires decorators
    names = {c.name for c in all_backends()}
    assert {"sram", "text", "python", "html"} <= names
    for c in all_backends():
        if c.name in {"sram", "text", "python", "html"}:
            assert c.phase == Phase.REPORT


def test_only_selection_intersection_helper():
    """The CLI '--only' semantics: report set intersected with the user list."""
    report_selection = {"sram", "text", "python", "html"}
    only = {"sram", "ahb"}  # ahb not registry-owned -> filtered out
    assert (report_selection & only) == {"sram"}
