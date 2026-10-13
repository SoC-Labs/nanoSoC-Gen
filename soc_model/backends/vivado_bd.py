# Copyright 2026, SoC Labs (www.soclabs.org)
"""Vivado block-diagram / IP-XACT packaging backend (roadmap doc 08).

Emits, from the SoC model, two model-driven TCL artifacts that replace the
hand-maintained ``pynq/vivado_ip/package_nanosoc_multicore_ip.tcl`` /
``nanosoc_multicore_design.tcl`` pair:

  * ``<top>_package_ip.tcl`` — an ``ipx::`` packaging script that types the
    FPGA wrapper's clocks / reset / UART (and, when present at the wrapper
    boundary, GPIO / AHB-Lite / APB / AXI) as Vivado IP-XACT *bus interfaces*,
    leaving SWD / RMII / MDIO / QSPI / status taps as standalone (raw) ports —
    exactly the discipline the live multicore script follows (STEP 3-8).
  * ``<top>_bd.tcl`` — an optional ``create_bd_*`` block design that
    instantiates the packaged IP, re-exposes its typed interfaces as
    ``create_bd_intf_port`` and its raw pins as ``create_bd_port``, and maps the
    model's interconnect targets via ``assign_bd_address`` (base/size = the
    single source of truth).

Scope is the bare / no-PS case: the board-level PS7/Zynq pin-mux glue
(``cmsdk_socket`` / xlslice / xlconcat / AXI-GPIO) is OUT of scope per the doc
— the generated BD is meant to be ``source``d as a sub-step from a small
hand-written board overlay.

The interface-inference table (§5.2) and the ``WRAPPER_BOUNDARY`` physical-name
indirection (§5.3.1) live in this module so the typing is driven from ONE data
source instead of the per-interface copy-paste in the hand TCL.

Jinja2 is an optional dependency: when it is absent the template-rendering
``generate_*`` methods no-op and return ``None`` (the ``chip.py`` convention).
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..model import Module
from ..utils import write_if_changed

try:
    from jinja2 import Environment, FileSystemLoader
except ImportError:                                    # pragma: no cover
    Environment = None
    FileSystemLoader = None

_BACKEND_DIR = Path(__file__).resolve().parent
_TEMPLATE_DIR = _BACKEND_DIR / 'templates'


# ===========================================================================
# (B) Interface inference table — model iface type -> Xilinx bus VLNV + map
# ===========================================================================
@dataclass
class PortMap:
    """One IP-XACT logical-port <-> physical-suffix mapping."""
    logical: str            # IP-XACT logical port, e.g. 'HADDR', 'TRI_I', 'CLK'
    suffix: str             # physical suffix appended to the iface name


@dataclass
class VivadoBusDef:
    """A Vivado bus type + abstraction + its logical port map."""
    bus_type_vlnv: str
    abstraction_vlnv: str
    # interface_mode Vivado expects; computed per iface direction when None.
    default_mode: Optional[str] = None
    port_maps: List[PortMap] = field(default_factory=list)
    custom_busdef: bool = False        # True -> a bus-def XML must be shipped
    display: str = ''                  # human display name


# --- Stock Xilinx defs (no XML needed) -------------------------------------
CLOCK = VivadoBusDef('xilinx.com:signal:clock:1.0',
                     'xilinx.com:signal:clock_rtl:1.0', 'slave',
                     [PortMap('CLK', '')], display='Clock')
RESET = VivadoBusDef('xilinx.com:signal:reset:1.0',
                     'xilinx.com:signal:reset_rtl:1.0', 'slave',
                     [PortMap('RST', '')], display='Reset')
GPIO = VivadoBusDef('xilinx.com:interface:gpio:1.0',
                    'xilinx.com:interface:gpio_rtl:1.0', 'master',
                    [PortMap('TRI_I', 'in'), PortMap('TRI_O', 'out'),
                     PortMap('TRI_T', 'outen')], display='GPIO')
UART = VivadoBusDef('xilinx.com:interface:uart:1.0',
                    'xilinx.com:interface:uart_rtl:1.0', 'master',
                    [PortMap('RxD', 'rxd'), PortMap('TxD', 'txd')],
                    display='UART')
AXIS = VivadoBusDef('xilinx.com:interface:axis:1.0',
                    'xilinx.com:interface:axis_rtl:1.0', None,
                    [PortMap('TVALID', 'tvalid'), PortMap('TREADY', 'tready'),
                     PortMap('TDATA', 'tdata'), PortMap('TLAST', 'tlast')],
                    display='AXI4-Stream')

# --- Custom AHB-Lite / APB defs (require shipped XML) -----------------------
AHB_LITE = VivadoBusDef(
    'soclabs.org:interface:ahblite:1.0',
    'soclabs.org:interface:ahblite_rtl:1.0', None,
    [PortMap('HADDR', 'haddr'), PortMap('HTRANS', 'htrans'),
     PortMap('HWRITE', 'hwrite'), PortMap('HSIZE', 'hsize'),
     PortMap('HBURST', 'hburst'), PortMap('HPROT', 'hprot'),
     PortMap('HWDATA', 'hwdata'), PortMap('HMASTLOCK', 'hmastlock'),
     PortMap('HRDATA', 'hrdata'), PortMap('HREADY', 'hready'),
     PortMap('HRESP', 'hresp'), PortMap('HSEL', 'hsel'),
     PortMap('HREADYOUT', 'hreadyout')],
    custom_busdef=True, display='AHB-Lite')
APB = VivadoBusDef(
    'soclabs.org:interface:apb:1.0',
    'soclabs.org:interface:apb_rtl:1.0', None,
    [PortMap('PSEL', 'psel'), PortMap('PADDR', 'paddr'),
     PortMap('PENABLE', 'penable'), PortMap('PWRITE', 'pwrite'),
     PortMap('PWDATA', 'pwdata'), PortMap('PRDATA', 'prdata'),
     PortMap('PREADY', 'pready'), PortMap('PSLVERR', 'pslverr')],
    custom_busdef=True, display='APB')

# Map model Interface.type -> VivadoBusDef (used for generator-promoted ifaces).
IFACE_TYPE_MAP = {
    'gpio': GPIO,
    'ahb': AHB_LITE,
    'apb': APB,
    'axis': AXIS,
    'axis_byte': AXIS,
    'uart': UART,
}


def interface_mode(iface, busdef: VivadoBusDef) -> str:
    """Vivado interface_mode for a model iface.

    AHB initiator/out -> master; target/in -> slave. APB always slave. Stock
    defs (clock/reset/uart/gpio) carry an explicit default_mode.
    """
    if busdef.default_mode:
        return busdef.default_mode
    if iface.type == 'apb':
        return 'slave'
    return 'master' if iface.is_output else 'slave'


# ===========================================================================
# (§5.3.1) Wrapper boundary: curated physical-name pinning per wrapper variant
# ===========================================================================
# Keyed by wrapper_top so the backend types against the wrapper that is actually
# packaged (NOT the raw model expansion). Each inner map is
# iface_name -> (VivadoBusDef, {logical: physical}). Physical names are copied
# verbatim from the wrapper module port list and MUST round-trip a unit test.
WRAPPER_BOUNDARY: Dict[str, Dict[str, Tuple[VivadoBusDef, Dict[str, str]]]] = {
    # Live multicore: two clocks + reset + one UART bus; everything else raw.
    'nanosoc_multicore_vivado_wrapper': {
        'sys_fclk':     (CLOCK, {'CLK': 'sys_fclk'}),
        'rmii_ref_clk': (CLOCK, {'CLK': 'rmii_ref_clk'}),   # +FREQ_HZ 50e6
        'nrst':         (RESET, {'RST': 'nrst'}),
        'uart':         (UART,  {'RxD': 'uart_rxd', 'TxD': 'uart_txd'}),
    },
    # Legacy single-core: clk/reset + two GPIO buses + one UART.
    'nanosoc_chip_vivado_wrapper': {
        'clk':   (CLOCK, {'CLK': 'clk'}),
        'nrst':  (RESET, {'RST': 'nrst'}),
        'gpio0': (GPIO,  {'TRI_I': 'gpio0_tri_i', 'TRI_O': 'gpio0_tri_o',
                          'TRI_T': 'gpio0_tri_t'}),
        'gpio1': (GPIO,  {'TRI_I': 'gpio1_tri_i', 'TRI_O': 'gpio1_tri_o',
                          'TRI_T': 'gpio1_tri_t'}),
        'uart':  (UART,  {'RxD': 'uart_rxd', 'TxD': 'uart_txd'}),
    },
}

# Per-wrapper clock association metadata: which clock carries FREQ_HZ, and which
# clock is the bus clock that ASSOCIATED_BUSIF hangs off.
_WRAPPER_CLOCK_FREQ_HZ = {
    'nanosoc_multicore_vivado_wrapper': {'rmii_ref_clk': 50000000},
    'nanosoc_chip_vivado_wrapper': {},
}

# Raw (standalone) pins emitted into the bare BD as create_bd_port. These mirror
# the wrapper's non-typed tail (SWD/RMII/MDIO/QSPI/status). Each entry is
# (name, dir, width). dir is Vivado 'I'/'O'. Used only by the optional BD TCL.
_WRAPPER_SCALAR_PORTS = {
    'nanosoc_multicore_vivado_wrapper': [
        ('sys_fclk', 'I', 1), ('rmii_ref_clk', 'I', 1), ('nrst', 'I', 1),
        ('cpu1_uart_rxd', 'I', 1), ('cpu1_uart_txd', 'O', 1),
        ('phy_rmii_crs_dv', 'I', 1), ('phy_rmii_rxd', 'I', 2),
        ('phy_rmii_rx_er', 'I', 1), ('phy_rmii_tx_en', 'O', 1),
        ('phy_rmii_txd', 'O', 2), ('phy_mdc', 'O', 1),
        ('mdio_i', 'I', 1), ('mdio_o', 'O', 1), ('mdio_t', 'O', 1),
        ('swd_clk', 'I', 1), ('swd_dio_i', 'I', 1),
        ('swd_dio_o', 'O', 1), ('swd_dio_t', 'O', 1),
        ('qspi_sclk', 'O', 1), ('qspi_ncs', 'O', 1),
        ('qspi_io_i', 'I', 4), ('qspi_io_o', 'O', 4), ('qspi_io_t', 'O', 4),
        ('eth_irq', 'O', 1), ('phc_pps_out', 'O', 1),
        ('sys_hresetn_o', 'O', 1), ('cpu1_wdog_reset', 'O', 1),
        ('sys_hclk_dbg', 'O', 1), ('uart_txd_dbg', 'O', 1),
    ],
    'nanosoc_chip_vivado_wrapper': [
        ('clk', 'I', 1), ('nrst', 'I', 1),
        ('swd_clk', 'I', 1), ('swd_dio_i', 'I', 1),
        ('swd_dio_o', 'O', 1), ('swd_dio_t', 'O', 1),
    ],
}


# ===========================================================================
# (C) The backend
# ===========================================================================
class SoCVivadoBdBackend:
    """Generate the Vivado IP-XACT packaging TCL + (optional) block design.

    Defaults target the LIVE multicore IP: the wrapper module name doubles as
    the IP name (the multicore board BD instantiates
    ``soclabs.org:user:nanosoc_multicore_vivado_wrapper:1.0``) and ``core_rev=1``
    matches ``pynq/Makefile`` ``FPGA_CORE_REV``. Pass the legacy triple
    (``wrapper_top='nanosoc_chip_vivado_wrapper'``, ``ip_name='nanosoc_chip'``,
    ``core_rev=2``) to re-emit the single-core IP instead.
    """

    def __init__(self, top_module: Module, vendor: str = 'soclabs.org',
                 core_rev: int = 1,
                 wrapper_top: str = 'nanosoc_multicore_vivado_wrapper',
                 ip_name: Optional[str] = None,
                 design_name: Optional[str] = None):
        self.top = top_module
        self.vendor = vendor
        self.core_rev = core_rev
        self.wrapper_top = wrapper_top
        self.ip_name = ip_name or wrapper_top
        self.design_name = design_name or f'{self.ip_name}_bd'

    # ------------------------------------------------------------------
    # VLNV helpers
    # ------------------------------------------------------------------
    @property
    def ip_vlnv(self) -> str:
        """The packaged IP VLNV the BD references (matches the live board BD)."""
        return f'{self.vendor}:user:{self.ip_name}:{self.core_rev}.0'

    # ------------------------------------------------------------------
    # typed boundary interfaces (the template context, §5.3.1)
    # ------------------------------------------------------------------
    def _clock_reset_assoc(self) -> dict:
        """ASSOCIATED_RESET / ASSOCIATED_BUSIF / reset POLARITY from the model.

        ASSOCIATED_RESET = the reset iface name typed on this wrapper;
        ASSOCIATED_BUSIF = the comma-joined names of every typed bus iface;
        POLARITY         = ACTIVE_LOW / ACTIVE_HIGH from the model reset.
        """
        boundary = WRAPPER_BOUNDARY.get(self.wrapper_top, {})
        reset_name = None
        bus_names: List[str] = []
        for name, (busdef, _phys) in boundary.items():
            if busdef is RESET:
                reset_name = name
            elif busdef not in (CLOCK, RESET):
                bus_names.append(name)
        # POLARITY from the model's reset domain (fallback to wrapper convention).
        polarity = 'ACTIVE_LOW'
        for r in self.top.resets:
            if reset_name and (r.name == reset_name or 'rst' in r.name.lower()):
                polarity = ('ACTIVE_LOW' if str(r.active).lower() in
                            ('low', 'active_low', '0') else 'ACTIVE_HIGH')
                break
        return {
            'reset_name': reset_name,
            'associated_busif': ','.join(bus_names),
            'polarity': polarity,
        }

    def _typed_entry(self, name: str, busdef: VivadoBusDef,
                     port_maps: List[Tuple[str, str]],
                     mode: Optional[str] = None,
                     assoc: Optional[dict] = None) -> dict:
        """Build one template interface-context dict."""
        is_clock = busdef is CLOCK
        is_reset = busdef is RESET
        assoc = assoc or {}
        freq_hz = _WRAPPER_CLOCK_FREQ_HZ.get(self.wrapper_top, {}).get(name)
        entry = {
            'name': name,
            'display': busdef.display or name,
            'bus_type_vlnv': busdef.bus_type_vlnv,
            'abstraction_vlnv': busdef.abstraction_vlnv,
            'mode': mode or busdef.default_mode or 'master',
            'custom_busdef': busdef.custom_busdef,
            'port_maps': [{'logical': lg, 'physical': ph}
                          for lg, ph in port_maps],
            'freq_hz': freq_hz,
            'polarity': assoc.get('polarity') if is_reset else None,
            # clocks carry the reset/busif association; buses carry none.
            'associated_reset': assoc.get('reset_name') if is_clock else None,
            'associated_busif': (assoc.get('associated_busif')
                                 if is_clock else None),
        }
        return entry

    def _typed_interfaces(self) -> List[dict]:
        """Resolve every typed boundary interface to a template context dict.

        Two passes: (1) curated, hard-pinned interfaces from WRAPPER_BOUNDARY
        (the hand-written wrapper renamed them); (2) generator-promoted model
        buses marked ``fpga_expose`` (physical names ARE the model expansion).
        """
        ctx: List[dict] = []
        assoc = self._clock_reset_assoc()
        boundary = WRAPPER_BOUNDARY.get(self.wrapper_top, {})
        # Pass 1 — curated.
        for name, (busdef, phys) in boundary.items():
            port_maps = [(pm.logical, phys[pm.logical]) for pm in
                         busdef.port_maps if pm.logical in phys]
            ctx.append(self._typed_entry(name, busdef, port_maps, assoc=assoc))
        # Pass 2 — promoted model buses (M5+).
        for iface in self.top.interfaces:
            if not iface.params.get('fpga_expose'):
                continue
            if iface.name in boundary:
                continue
            busdef = IFACE_TYPE_MAP.get(iface.type)
            if busdef is None:
                continue
            port_maps = [(pm.logical, f'{iface.name}_{pm.suffix}')
                         for pm in busdef.port_maps]
            ctx.append(self._typed_entry(
                iface.name, busdef, port_maps,
                mode=interface_mode(iface, busdef), assoc=assoc))
        return ctx

    def _bus_def_xmls(self) -> List[str]:
        """Custom bus-def XML basenames referenced by any typed interface."""
        seen, out = set(), []
        for bif in self._typed_interfaces():
            if bif['custom_busdef'] and bif['bus_type_vlnv'] not in seen:
                seen.add(bif['bus_type_vlnv'])
                stem = bif['name']
                out.append(f'{stem}_busdef.xml')
        return out

    # ------------------------------------------------------------------
    # raw / scalar boundary ports + address map (for the BD TCL)
    # ------------------------------------------------------------------
    def _typed_iface_names(self) -> set:
        return {bif['name'] for bif in self._typed_interfaces()}

    def _scalar_ports(self) -> List[dict]:
        """Raw (standalone) pins re-exposed in the bare BD as create_bd_port.

        These are the wrapper's non-typed tail; clocks/reset that are *typed*
        as bus interfaces are still emitted as scalar ports for the bare BD so
        the IP's clock/reset pins have a driver in the no-PS case.
        """
        out: List[dict] = []
        for name, vdir, width in _WRAPPER_SCALAR_PORTS.get(self.wrapper_top, []):
            out.append({'name': name, 'dir': vdir, 'width': width})
        return out

    def _intf_ports(self) -> List[dict]:
        """Typed bus interfaces re-exposed in the BD as create_bd_intf_port.

        Clocks/resets are driven by scalar nets in the bare BD, so only true
        bus interfaces (uart/gpio/ahb/apb/axis) become intf ports.
        """
        out: List[dict] = []
        for bif in self._typed_interfaces():
            if bif['bus_type_vlnv'] in (CLOCK.bus_type_vlnv, RESET.bus_type_vlnv):
                continue
            out.append({
                'name': bif['name'],
                'mode': 'Master' if bif['mode'] == 'master' else 'Slave',
                'abstraction_vlnv': bif['abstraction_vlnv'],
            })
        return out

    def _address_segments(self) -> List[dict]:
        """assign_bd_address segments from the model's interconnect targets.

        offset = InterconnectTarget.base, range = InterconnectTarget.size —
        the model is the single source of truth (kills hardcoded 0x4120_0000
        drift). The space/seg path strings depend on the packaged IP's
        auto-generated segment names (observable only after a real Vivado
        packaging run), so they are emitted as model-derived placeholders.
        """
        out: List[dict] = []
        for ic in self.top.interconnects:
            for tgt in ic.targets:
                if not tgt.size:
                    continue
                out.append({
                    'name': tgt.name,
                    'base': int(tgt.base),
                    'size': int(tgt.size),
                    'space': f'nanosoc_0/{ic.name}',
                    'seg': f'nanosoc_0/{tgt.name}/Reg',
                })
        return out

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------
    def _env(self):
        return Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )

    def render_package_tcl(self) -> Optional[str]:
        """Render the ipx:: packaging TCL string (None without jinja2)."""
        if Environment is None:                        # pragma: no cover
            return None
        tmpl = self._env().get_template('vivado_package_ip.tcl.j2')
        return tmpl.render(
            wrapper_top=self.wrapper_top,
            ip_name=self.ip_name,
            vendor=self.vendor,
            core_rev=self.core_rev,
            interfaces=self._typed_interfaces(),
            bus_def_xmls=self._bus_def_xmls(),
        )

    def render_bd_tcl(self, with_ps: bool = False) -> Optional[str]:
        """Render the create_bd_* TCL string (None without jinja2)."""
        if Environment is None:                        # pragma: no cover
            return None
        tmpl = self._env().get_template('vivado_bd.tcl.j2')
        return tmpl.render(
            design_name=self.design_name,
            ip_vlnv=self.ip_vlnv,
            vendor=self.vendor,
            ip_name=self.ip_name,
            rev=self.core_rev,
            with_ps=with_ps,
            scalar_ports=self._scalar_ports(),
            intf_ports=self._intf_ports(),
            address_segments=self._address_segments(),
        )

    def generate_package_tcl(self, out_dir: Path) -> Optional[Path]:
        """Emit ``<top>_package_ip.tcl``. Returns the path (None w/o jinja2)."""
        content = self.render_package_tcl()
        if content is None:                            # pragma: no cover
            return None
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / f'{self.ip_name}_package_ip.tcl'
        write_if_changed(p, content)
        return p

    def generate_bd_tcl(self, out_dir: Path,
                        with_ps: bool = False) -> Optional[Path]:
        """Emit ``<top>_bd.tcl``. Returns the path (None w/o jinja2)."""
        content = self.render_bd_tcl(with_ps=with_ps)
        if content is None:                            # pragma: no cover
            return None
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / f'{self.ip_name}_bd.tcl'
        write_if_changed(p, content)
        return p

    def generate(self, out_dir: Path,
                 emit_package: bool = True,
                 emit_bd: bool = True,
                 with_ps: bool = False) -> List[Path]:
        """Write the packaging + (optional) BD TCL. Returns written paths.

        Mirrors the SoCConstraintsBackend.generate convention: returns the list
        of written Paths (empty when jinja2 is absent).
        """
        written: List[Path] = []
        if emit_package:
            p = self.generate_package_tcl(out_dir)
            if p is not None:
                written.append(p)
        if emit_bd:
            p = self.generate_bd_tcl(out_dir, with_ps=with_ps)
            if p is not None:
                written.append(p)
        return written
