# Copyright 2026, SoC Labs (www.soclabs.org)
"""GEN-BACKLOG 20: bundle members of a hand-written (gen: False) module.

The validator recognised a bundle member only by its exact generator spelling,
`<iface>_<lowercase suffix>` (bus_member_names). Hand-written RTL spells them
otherwise, so the QuickStart top printed 54 false "Port '...' not found"
reports:
  1. the suffix in upper case        USRT0_TXD_TVALID, P0_IN            (38)
  2. APB as <base minus _APB>_P<SIG>  DEBUG_PSEL                         (8)
  3. axis_byte tlast (not in the table) txd_tlast                        (2)
  4. an RTL direction suffix _o/_i    ADP_RXD_TVALID_o                   (6)
Each is now recognised on gen: False modules only. A name that is none of these
(the planted typo USRT0_TXD_TVALIX) is still reported, and gen: True modules
keep exact matching: the generator emits exactly those names.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from soc_model.adapters.registry import protocol_for
from soc_model.model import Connection, Instance, Interface, Module
from soc_model.validator import SoCValidator
from tests.helpers import arm_free_env

pytestmark = pytest.mark.unit

PKG_ROOT = Path(__file__).resolve().parents[2]
REPRO = PKG_ROOT / "tests" / "fixtures" / "bundle_ports"


def _hw_periph(gen=False):
    return Module(name="hw_periph", gen=gen, interfaces=[
        Interface("HCLK", type="wire", direction="in", params={"WIDTH": 1}),
        Interface("USRT0_TXD", type="axis_byte", direction="out", params={}),
        Interface("P0", type="gpio", direction="inout", params={"WIDTH": 16}),
        Interface("DEBUG_APB", type="apb", direction="target",
                  params={"ADDR_WIDTH": 10, "DATA_WIDTH": 32}),
        Interface("txd", type="axis_byte", direction="out", params={}),
        Interface("ADP_RXD", type="axis_byte", direction="out", params={}),
        # Review L1 (Train 8): ifaces that give the alias and tlast rules no
        # protocol of their own to hide behind.
        Interface("S_AXIS", type="axis", direction="in", params={}),
        Interface("AHB", type="ahb", direction="target", params={}),
        Interface("UART_CFG", type="apb", direction="target",
                  params={"ADDR_WIDTH": 12, "DATA_WIDTH": 32}),
    ])


def _not_found(child, ports):
    top = Module("t", instances=[Instance(
        instance_name="u", module_name=child.name,
        connections=[Connection(p, "unconnected") for p in ports],
        resolved_module=child)])
    return sorted(m.port for m in SoCValidator(top).validate_all()
                  if m.category == "reference" and "not found on module" in m.message)


HAND_WRITTEN = [
    "USRT0_TXD_TVALID", "USRT0_TXD_TDATA", "USRT0_TXD_TREADY", "P0_IN", "P0_OUTEN",   # 1
    "DEBUG_PSEL", "DEBUG_PADDR", "DEBUG_PRDATA", "DEBUG_PSLVERR",                        # 2
    "txd_tlast", "TXD_TLAST",                                                            # 3
    "ADP_RXD_TVALID_o", "ADP_RXD_TDATA_o", "ADP_RXD_TREADY_i",                           # 4
]


@pytest.mark.parametrize("port", HAND_WRITTEN)
def test_hand_written_member_spellings_are_recognised(port):
    assert _not_found(_hw_periph(), [port]) == []


TYPOS = [
    "USRT0_TXD_TVALIX",       # the planted control
    "USRT0_TXD_TVALID_x",     # not a direction suffix
    "USRT0_TXD_TVALID_o_o",   # only ONE direction suffix
    "USRT0_TX_TVALID",        # base misspelt
    "P0_INN",
    "DEBUG_PSELX",
    "DEBUGX_PSEL",
    "DEBUG_APB_PSELX",
    "DEBUG_TVALID",           # APB base, not an APB member
    "USRT0_PSEL",             # the _APB alias is APB's only, and needs a _APB base
    "txd_tuser",              # only tlast is the extra axis_byte member
    "ADP_RXD_TLAST_x",
    # Review L1 (Train 8): each fails if a recognition rule leaks beyond its scope.
    "S_AXIS_PSEL",            # the APB members exist on APB ifaces only
    "P0_TLAST",               # tlast is axis_byte's extra member only (gpio)
    "AHB_TLAST",              # (ahb)
    "DEBUG_TLAST",            # (apb, through its _APB alias)
    "UART_PSEL",              # the APB alias strips _APB only, not any _X (UART_CFG)
]


@pytest.mark.parametrize("port", TYPOS)
def test_a_real_typo_is_still_reported(port):
    assert _not_found(_hw_periph(), [port]) == [port]


@pytest.mark.parametrize("port", ["USRT0_TXD_TVALID", "DEBUG_PSEL", "txd_tlast", "ADP_RXD_TVALID_o"])
def test_gen_true_modules_keep_exact_matching(port):
    # The generator emits exactly <iface>_<suffix>; any other spelling on a
    # generated module is a real mismatch.
    assert _not_found(_hw_periph(gen=True), [port]) == [port]


def test_generated_names_still_recognised_on_both():
    for gen in (False, True):
        assert _not_found(_hw_periph(gen), ["USRT0_TXD_tvalid", "P0_in", "DEBUG_APB_psel"]) == []


def test_the_generator_tables_do_not_change():
    # tlast and the APB alias are recognition only: nothing is expanded or emitted.
    txd = Interface("txd", type="axis_byte", direction="out", params={})
    p = protocol_for(txd)
    assert p.member_names(txd) == ["txd_tvalid", "txd_tready", "txd_tdata"]
    assert [s.name for s in p.expand(txd, "")] == ["txd_tvalid", "txd_tready", "txd_tdata"]
    apb = Interface("DEBUG_APB", type="apb", direction="target", params={})
    assert all(n.startswith("DEBUG_APB_") for n in protocol_for(apb).member_names(apb))


def test_repro_reports_only_the_planted_typo():
    """The superproject repro (dd33c2b): 8 reports before, 1 (the control) now."""
    env = arm_free_env()
    env.update(PYTHONPATH=str(PKG_ROOT), PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, "-m", "soc_model", str(REPRO / "repro_top.yaml"),
                        "-l", str(REPRO / "lib"), "--validate-only"], cwd=str(PKG_ROOT), env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       universal_newlines=True, timeout=300)
    found = re.findall(r"Port '([^']*)' not found on module 'hw_periph'", r.stdout)
    assert found == ["USRT0_TXD_TVALIX"], r.stdout[-3000:]
