# Copyright 2026, SoC Labs (www.soclabs.org)
"""Unit tests for SoCTbBackend (testbench generation — roadmap doc 04).

The backend emits, for any top-level Module, a self-contained cocotb env:
  * tb_top.sv       — clock/reset gen + DUT instantiation with model-derived
                      boundary tie-offs, plus an in-TB hostio4_target host
                      bridge when the top exposes the hostio4_p1_* pad bus,
  * model_summary.py— flat importable dicts (TARGETS/ACCESS_MODE/REGMAPS/...),
  * test_default.py — the model-derived TBGEN_* default battery,
  * Makefile        — parameterised cocotb env boilerplate.

These are pure-Python tests (no EDA tool, no cocotb run): build a fixture
Module shaped like the real multicore top (a pad-bus HOSTIO4 triplet, a uart
output, a writable memory target, and a periph target carrying a PID/CID
RegisterMap with reset values, plus an *irq output), run the backend into a
tmp dir, and assert the emitted artifacts.
"""

import importlib.util
from pathlib import Path

import pytest

from soc_model.model import (
    Module, Clock, Reset, Interface, Instance, Interconnect,
    InterconnectTarget, InterconnectInitiator, InterconnectInitiatorTarget,
    RegisterMap, Register, AddressDecode, AddressDecodeSlot,
)
from soc_model.backends.tb import SoCTbBackend

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# fixture Module — built programmatically (like test_constraints.py's inline
# Module tests) so the test owns every shape it asserts on.
# ---------------------------------------------------------------------------
def _pidcid_regmap():
    regs = []
    # PID0..3 @ 0xFE0/4/8/C, CID0..3 @ 0xFF0/4/8/C — CMSDK-style identity.
    for i, off in enumerate((0xFE0, 0xFE4, 0xFE8, 0xFEC)):
        regs.append(Register(name=f"PID{i}", offset=off, access="RO",
                             reset_value=(0x22, 0x10, 0x00, 0x00)[i]))
    for i, off in enumerate((0xFF0, 0xFF4, 0xFF8, 0xFFC)):
        regs.append(Register(name=f"CID{i}", offset=off, access="RO",
                             reset_value=(0x0D, 0xF0, 0x05, 0xB1)[i]))
    regs.append(Register(name="CTRL", offset=0x000, access="RW",
                         reset_value=0x0))
    return RegisterMap(name="phc_ctrl", module="phc", registers=regs)


def _fixture_top():
    top = Module(name="tbgen_soc", gen=True)
    top.params = {}  # SYS_ADDR_W/DATA_W default to symbolic refs (fine for TB)
    top.clocks.append(Clock(name="sys_fclk", source="external", freq_hz=100_000_000))
    top.resets.append(Reset(name="sys_sysresetn", active="low", source="external"))

    # Boundary interfaces (the shape that forces ACCESS_MODE='adp').
    top.interfaces += [
        Interface(name="sys_fclk", type="wire", direction="in",
                  params={"WIDTH": 1}),
        Interface(name="sys_sysresetn", type="wire", direction="in",
                  params={"WIDTH": 1}),
        Interface(name="hostio4_p1_in", type="wire", direction="in",
                  params={"WIDTH": 7}),
        Interface(name="hostio4_p1_out", type="wire", direction="out",
                  params={"WIDTH": 7}),
        Interface(name="hostio4_p1_outen", type="wire", direction="out",
                  params={"WIDTH": 7}),
        Interface(name="uart_txd", type="wire", direction="out",
                  params={"WIDTH": 1}),
        Interface(name="phc_pps_irq", type="wire", direction="out",
                  params={"WIDTH": 1}),
        # An input that must be tied to a non-zero constant (exact-name dict).
        Interface(name="qspi_io_i", type="wire", direction="in",
                  params={"WIDTH": 4}),
        # A generic multi-bit input -> {WIDTH{1'b0}} default tie-off.
        Interface(name="cpu_0_pmuenable", type="wire", direction="in",
                  params={"WIDTH": 1}),
    ]

    # A periph instance carrying a PID/CID regmap via its address_decode.
    phc = Module(name="phc", gen=False)
    phc.address_decode = AddressDecode(
        type="apb_slave_mux",
        slots=[AddressDecodeSlot(slot=0, name="phc_0",
                                 resolved_register_map=_pidcid_regmap())],
    )
    phc_inst = Instance(instance_name="u_phc_0", module_name="phc")
    phc_inst.resolved_module = phc
    top.instances.append(phc_inst)

    ic = Interconnect(name="sys_ahb_interconnect")
    ic.targets = [
        InterconnectTarget(name="cc_imem_0", instance="u_imem_0",
                           base=0x10000000, size=0x4000, sw_access="rwx",
                           region_type="memory"),
        InterconnectTarget(name="phc_0", instance="u_phc_0",
                           base=0x22000000, size=0x01000000, sw_access="rw",
                           region_type="periph"),
    ]
    ic.initiators = [
        InterconnectInitiator(name="debug_m", targets=[
            InterconnectInitiatorTarget(name="cc_imem_0"),
            InterconnectInitiatorTarget(name="phc_0"),
        ]),
    ]
    top.interconnects.append(ic)
    return top


def _gen(tmp_path, overrides=None):
    top = _fixture_top()
    be = SoCTbBackend(top)
    out = tmp_path / "verification"
    path = be.generate(out, config_overrides=overrides)
    return out, path


def _load_summary(out_dir):
    spec = importlib.util.spec_from_file_location(
        "model_summary_under_test", out_dir / "model_summary.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ===========================================================================
# file emission
# ===========================================================================
class TestEmission:
    def test_all_files_emitted(self, tmp_path):
        out, path = _gen(tmp_path)
        assert path == out / "tb_top.sv" and path.exists()
        for name in ("tb_top.sv", "model_summary.py", "test_default.py",
                     "Makefile"):
            f = out / name
            assert f.exists() and f.read_text(), name

    def test_returns_none_without_jinja(self, tmp_path, monkeypatch):
        import soc_model.backends.tb as tbmod
        monkeypatch.setattr(tbmod, "Environment", None)
        assert SoCTbBackend(_fixture_top()).generate(tmp_path / "v") is None


# ===========================================================================
# tb_top.sv — boundary + tie-offs + bridge
# ===========================================================================
class TestTbTop:
    def test_clock_and_reset_decls(self, tmp_path):
        out, _ = _gen(tmp_path)
        sv = (out / "tb_top.sv").read_text()
        assert "module tb_top;" in sv
        assert "reg sys_fclk;" in sv
        assert "always #(FCLK_PERIOD_NS/2.0) sys_fclk = ~sys_fclk;" in sv
        assert "reg sys_sysresetn;" in sv
        # active-low reset: asserted 0, released 1
        assert ("sys_sysresetn = 1'b0; #200; sys_sysresetn = 1'b1;") in sv

    def test_period_from_freq(self, tmp_path):
        out, _ = _gen(tmp_path)
        sv = (out / "tb_top.sv").read_text()
        assert "parameter FCLK_PERIOD_NS = 10;" in sv  # 100 MHz -> 10 ns

    def test_dut_instantiated(self, tmp_path):
        out, _ = _gen(tmp_path)
        sv = (out / "tb_top.sv").read_text()
        assert "tbgen_soc" in sv
        assert "u_dut (" in sv
        assert ".sys_fclk(sys_fclk)" in sv
        assert ".sys_sysresetn(sys_sysresetn)" in sv

    def test_output_probe_wires(self, tmp_path):
        out, _ = _gen(tmp_path)
        sv = (out / "tb_top.sv").read_text()
        assert "wire  uart_txd;" in sv or "wire uart_txd;" in sv
        assert "wire  phc_pps_irq;" in sv or "wire phc_pps_irq;" in sv
        assert ".uart_txd(uart_txd)" in sv

    def test_input_tieoffs(self, tmp_path):
        out, _ = _gen(tmp_path)
        sv = (out / "tb_top.sv").read_text()
        # exact-name special case
        assert ".qspi_io_i(4'hF)" in sv
        # default zero tie-off (NOT driven high by any _enable suffix rule)
        assert ".cpu_0_pmuenable(1'b0)" in sv

    def test_hostio4_bridge_emitted(self, tmp_path):
        out, _ = _gen(tmp_path)
        sv = (out / "tb_top.sv").read_text()
        assert "hostio4_target u_hostio4_host (" in sv
        assert "axis_rx0_tvalid" in sv and "axis_tx0_tdata8" in sv
        assert "wire CLK  = sys_fclk;" in sv
        assert "wire NRST = sys_sysresetn;" in sv
        # DUT wired to the PADS, not axis_*
        assert ".hostio4_p1_in(hostio4_p1_in)" in sv
        assert ".hostio4_p1_out(hostio4_p1_out)" in sv
        assert ".hostio4_p1_outen(hostio4_p1_outen)" in sv

    def test_config_overrides_passed(self, tmp_path):
        out, _ = _gen(tmp_path,
                       overrides={"ETH_IMEM_MEM_FPGA_IMG": '"sim_build/image.hex"'})
        sv = (out / "tb_top.sv").read_text()
        assert '.ETH_IMEM_MEM_FPGA_IMG("sim_build/image.hex")' in sv


# ===========================================================================
# model_summary.py — importable, matches the fixture
# ===========================================================================
class TestModelSummary:
    def test_imports_and_top_fields(self, tmp_path):
        out, _ = _gen(tmp_path)
        M = _load_summary(out)
        assert M.TOP_MODULE == "tbgen_soc"
        assert M.ACCESS_MODE == "adp"
        assert M.HOSTIO4_BRIDGE is True
        assert M.CLK_ALIAS == "sys_fclk"
        assert M.NRST_ALIAS == "sys_sysresetn"
        assert M.CLOCKS == ["sys_fclk"]
        assert M.RESETS == [{"name": "sys_sysresetn", "active": "low"}]

    def test_targets_match_fixture(self, tmp_path):
        out, _ = _gen(tmp_path)
        M = _load_summary(out)
        by_name = {t["name"]: t for t in M.TARGETS}
        assert by_name["cc_imem_0"]["base"] == 0x10000000
        assert by_name["cc_imem_0"]["size"] == 0x4000
        assert by_name["cc_imem_0"]["region_type"] == "memory"
        assert by_name["phc_0"]["base"] == 0x22000000
        # region_type uses the model's own vocab: 'periph' (NOT 'peripheral')
        assert by_name["phc_0"]["region_type"] == "periph"

    def test_regmap_identity_derived(self, tmp_path):
        out, _ = _gen(tmp_path)
        M = _load_summary(out)
        by_name = {t["name"]: t for t in M.TARGETS}
        rm = by_name["phc_0"]["regmap"]
        assert rm is not None
        assert rm["PID"] == [0x22, 0x10, 0x00, 0x00]
        assert rm["CID"] == [0x0D, 0xF0, 0x05, 0xB1]
        # the memory target has no identity regmap
        assert by_name["cc_imem_0"]["regmap"] is None
        # REGMAPS only carries identity-bearing targets
        assert "phc_0" in M.REGMAPS and "cc_imem_0" not in M.REGMAPS

    def test_initiators_and_irqs(self, tmp_path):
        out, _ = _gen(tmp_path)
        M = _load_summary(out)
        inits = {i["name"]: i for i in M.INITIATORS}
        assert "debug_m" in inits
        assert set(inits["debug_m"]["targets"]) == {"cc_imem_0", "phc_0"}
        assert M.IRQS == ["phc_pps_irq"]


# ===========================================================================
# test_default.py — the TBGEN_* battery
# ===========================================================================
class TestDefaultTests:
    def test_imports_model_summary(self, tmp_path):
        out, _ = _gen(tmp_path)
        py = (out / "test_default.py").read_text()
        assert "import model_summary as M" in py
        assert "import cocotb" in py

    def test_all_tbgen_ids_present(self, tmp_path):
        out, _ = _gen(tmp_path)
        py = (out / "test_default.py").read_text()
        for tid in ("TBGEN_CLK_001", "TBGEN_BOOT_001", "TBGEN_ENUM_001",
                    "TBGEN_REG_001", "TBGEN_MEM_001", "TBGEN_IRQ_001"):
            assert tid in py, tid

    def test_bus_tests_active_on_adp(self, tmp_path):
        out, _ = _gen(tmp_path)
        py = (out / "test_default.py").read_text()
        # with a bus path the driver import + read32 loop are emitted
        assert "from nanosoc_cocotb_driver import NanoSoC" in py
        assert "await soc.read32(t[\"base\"])" in py

    def test_boot_and_irq_active(self, tmp_path):
        out, _ = _gen(tmp_path)
        py = (out / "test_default.py").read_text()
        # uart present -> BOOT not a skip stub; irq present -> IRQ not a skip stub
        assert 'getattr(dut, "uart_txd"' in py
        assert "for name in M.IRQS:" in py

    def test_compiles_as_python(self, tmp_path):
        import ast
        out, _ = _gen(tmp_path)
        ast.parse((out / "test_default.py").read_text())


# ===========================================================================
# Makefile
# ===========================================================================
class TestMakefile:
    def test_toplevel_module_and_sources(self, tmp_path):
        out, _ = _gen(tmp_path)
        mk = (out / "Makefile").read_text()
        assert "TOPLEVEL  = tb_top" in mk
        assert "MODULE    = test_default" in mk
        assert "VERILOG_SOURCES += $(CURDIR)/tb_top.sv" in mk

    def test_hostio4_sources_appended(self, tmp_path):
        out, _ = _gen(tmp_path)
        mk = (out / "Makefile").read_text()
        # the five hostio4_target sources (not on the flist) are added
        for src in ("hostio4_target_sync", "hostio4_target_fsm",
                    "hostio4_target_axis_rxport", "hostio4_target_axis_txport",
                    "hostio4_target"):
            assert f"{src}.v" in mk, src


# ===========================================================================
# access-mode degradation (no pads, no axis, no uart -> 'none')
# ===========================================================================
class TestAccessModeDegradation:
    def _bare_top(self):
        top = Module(name="bare_soc", gen=True)
        top.params = {}
        top.clocks.append(Clock(name="clk", source="external"))
        top.resets.append(Reset(name="rstn", active="low"))
        top.interfaces += [
            Interface(name="clk", type="wire", direction="in",
                      params={"WIDTH": 1}),
            Interface(name="rstn", type="wire", direction="in",
                      params={"WIDTH": 1}),
            Interface(name="gpio_o", type="wire", direction="out",
                      params={"WIDTH": 8}),
        ]
        return top

    def test_none_access_skips_bus_tests(self, tmp_path):
        be = SoCTbBackend(self._bare_top())
        out = tmp_path / "v"
        be.generate(out)
        M = _load_summary(out)
        assert M.ACCESS_MODE == "none"
        assert M.HOSTIO4_BRIDGE is False
        py = (out / "test_default.py").read_text()
        # no bridge -> no hostio4_target in the TB
        assert "hostio4_target" not in (out / "tb_top.sv").read_text()
        # bus tests gated as skips
        assert "no bus access path" in py
        # BOOT + IRQ degrade to skip stubs (no uart, no irq)
        assert "@cocotb.test(skip=True)" in py

    def test_uart_only_top_boots_but_skips_bus(self, tmp_path):
        top = self._bare_top()
        top.interfaces.append(Interface(name="uart_txd", type="wire",
                                        direction="out", params={"WIDTH": 1}))
        out = tmp_path / "v"
        SoCTbBackend(top).generate(out)
        M = _load_summary(out)
        assert M.ACCESS_MODE == "uart"
        py = (out / "test_default.py").read_text()
        assert 'getattr(dut, "uart_txd"' in py
        assert "no bus access path" in py
