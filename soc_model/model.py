"""Object-oriented data model for SoC system descriptions."""

try:
    from dataclasses import dataclass, field
except ImportError:
    raise ImportError(
        "The 'dataclasses' module is required. "
        "On Python 3.6, install the backport: pip install dataclasses"
    )

from typing import Any, Dict, List, Optional


@dataclass
class Param:
    """A design parameter."""
    name: str
    type: str = 'int'
    default: Any = None
    desc: str = ''


@dataclass
class Clock:
    """A clock domain.

    ``freq_hz`` (or ``period_ns``) is an OPTIONAL per-clock frequency used by
    the constraints backend to emit ``create_clock``.  When neither is set the
    backend falls back to the top's ``SYS_CLK_FREQ_HZ`` for the primary system
    clock and to a declared ratio for derived clocks.  ``port`` overrides the
    physical boundary port the clock is created on (defaults to the boundary
    pad whose net matches ``name``).
    """
    name: str
    source: str = ''
    desc: str = ''
    freq_hz: Optional[int] = None     # explicit clock frequency in Hz
    period_ns: Optional[float] = None  # explicit clock period in ns
    port: Optional[str] = None         # physical port the clock is created on


@dataclass
class GeneratedClock:
    """A generated (derived) clock — divide/multiply of a source clock.

    Mirrors SDC create_generated_clock / XDC create_generated_clock.  ``source``
    names a parent Clock (by name) OR a source pin/port; ``divide_by`` /
    ``multiply_by`` set the ratio.  ``source_pin`` / ``target_pin`` are the
    hierarchical pin globs used on FPGA (Vivado) where the divided clock lives
    on an internal register (e.g. the RMII /2 divider, the QSPI SCK divider).
    """
    name: str
    source: str = ''                   # parent clock name (or source port)
    divide_by: Optional[int] = None
    multiply_by: Optional[int] = None
    source_pin: Optional[str] = None   # hierarchical source pin glob (FPGA)
    target_pin: Optional[str] = None   # hierarchical generated-clock pin glob
    desc: str = ''


@dataclass
class IoDelay:
    """An input/output delay constraint on a set of boundary ports.

    ``ports`` is a list of boundary-port globs (e.g. ``["qspi_io[*]"]``);
    ``clock`` names the reference clock; ``kind`` is ``input`` or ``output``;
    ``min``/``max`` are the delay values in ns (either or both may be given).
    """
    kind: str = 'input'                # input | output
    clock: str = ''                    # reference clock name
    ports: List[str] = field(default_factory=list)
    min: Optional[float] = None
    max: Optional[float] = None
    desc: str = ''


@dataclass
class FalsePath:
    """A set_false_path constraint (timing exception).

    Any of ``from_`` / ``to`` / ``through`` may be set (boundary ports, clocks,
    or pad-cell globs).  ``hold`` restricts to ``-hold`` only.
    """
    from_: Optional[str] = None
    to: Optional[str] = None
    through: Optional[str] = None
    hold: bool = False
    desc: str = ''


@dataclass
class MulticyclePath:
    """A set_multicycle_path constraint."""
    cycles: int = 2
    setup: bool = False
    hold: bool = False
    end: bool = False
    from_: Optional[str] = None
    to: Optional[str] = None
    through: Optional[str] = None
    desc: str = ''


@dataclass
class ClockGroup:
    """An asynchronous clock grouping (set_clock_groups -asynchronous).

    ``groups`` is a list of lists of clock names; clocks WITHIN a sub-list are
    mutually synchronous, clocks in DIFFERENT sub-lists are asynchronous.
    """
    name: str = ''
    groups: List[List[str]] = field(default_factory=list)
    desc: str = ''


@dataclass
class ConstraintSet:
    """A module's declared timing constraints (the ``constraints:`` block).

    Authored on a PRE-VERIFIED IP module so the IP carries the clocks /
    generated-clocks / false-paths / multicycle / io-delays / clock-groups it
    needs; the constraints backend AGGREGATES these from every resolved
    instance up to the top-level SDC/XDC.

    ``scope`` selects which technologies a sub-constraint applies to:
      * ``both``  (default) — emitted to both SDC and XDC,
      * ``asic`` / ``sdc`` — SDC only,
      * ``fpga`` / ``xdc`` — XDC only.
    Each sub-entry may carry its own ``scope`` (overriding the set default).
    """
    scope: str = 'both'
    clocks: List[Clock] = field(default_factory=list)
    generated_clocks: List[GeneratedClock] = field(default_factory=list)
    io_delays: List[IoDelay] = field(default_factory=list)
    false_paths: List[FalsePath] = field(default_factory=list)
    multicycle_paths: List[MulticyclePath] = field(default_factory=list)
    clock_groups: List[ClockGroup] = field(default_factory=list)
    # Per-entry scope overrides are stored on the entry itself via a parallel
    # ``_scope`` attribute set by the builder (kept off the public dataclasses
    # so the simple cases stay clean); see SoCConstraintsBackend._scope_of.


@dataclass
class Reset:
    """A reset domain."""
    name: str
    active: str = 'low'
    source: str = ''
    desc: str = ''


@dataclass
class InterfaceSignal:
    """A signal within a protocol interface definition."""
    name: str
    direction: str = 'in'  # in, out
    width: Any = 1  # int or str expression
    desc: str = ''


@dataclass
class InterfaceDefinition:
    """A protocol interface definition (from interfaces/ YAML files)."""
    name: str
    protocol: str = ''
    role: str = ''
    params: Dict[str, Any] = field(default_factory=dict)
    signals: List[InterfaceSignal] = field(default_factory=list)


@dataclass
class Interface:
    """A port/interface on a module."""
    name: str
    type: str = 'wire'  # wire, ahb, apb, axis, axis_byte, swd, gpio
    direction: str = 'in'  # in, out, inout, initiator, target, receiver
    params: Dict[str, Any] = field(default_factory=dict)
    desc: str = ''

    @property
    def width(self) -> Optional[int]:
        """Get the width of a wire interface."""
        if self.type == 'wire':
            w = self.params.get('WIDTH', 1)
            if isinstance(w, int):
                return w
        return None

    @property
    def addr_width(self) -> Optional[int]:
        """Get address width for bus interfaces."""
        w = self.params.get('ADDR_WIDTH') or self.params.get('ADDR_W')
        return w if isinstance(w, int) else None

    @property
    def data_width(self) -> Optional[int]:
        """Get data width for bus/stream interfaces."""
        w = self.params.get('DATA_WIDTH') or self.params.get('DATA_W')
        if w is None and self.type == 'axis_byte':
            return 8
        return w if isinstance(w, int) else None

    @property
    def is_input(self) -> bool:
        return self.direction in ('in', 'target', 'receiver')

    @property
    def is_output(self) -> bool:
        return self.direction in ('out', 'initiator', 'sender')

    @property
    def is_bidirectional(self) -> bool:
        return self.direction == 'inout'


@dataclass
class Connection:
    """A port-to-signal connection on an instance."""
    port: str
    conn: str  # signal reference, or 'unconnected' for intentionally open ports
    desc: str = ''

    @property
    def is_unconnected(self) -> bool:
        return self.conn == 'unconnected'


@dataclass
class Assign:
    """A combinational assign statement (legacy format)."""
    target: str
    expr: str
    type: Optional[str] = None  # 'interrupt' or None
    bit: Any = None  # int, str like "31:16", or "nmi"
    combining: Optional[str] = None  # 'OR', 'AND'
    origin: Optional[str] = None
    desc: str = ''


@dataclass
class GlueLogicEntry:
    """A typed glue logic entry — maps to a structural helper module instance.

    Types:
      passthrough — connects input directly to output
      or_reduce   — reduction OR of multi-bit input to 1-bit output
      or_combine  — N-input OR of equal-width operands
      and_gate    — 2-input AND
      constant    — drives output to a fixed value
      concat      — per-bit concatenation of 1-bit signals into a multi-bit
                    output net; unmapped bits (and bits >= pad_zero_above)
                    are driven to 1'b0
      mux2        — 2:1 multiplexer: out = select ? inputs[1] : inputs[0].
                    `select` is typically a module parameter ref ($PARAM) so a
                    build-time-constant select collapses to a direct wire. Used
                    for clock/reset source selection (e.g. CLK_RST_CONSUMER).
      reset_sync  — async-assert / sync-deassert reset synchroniser.
                    inputs = [arstn, clk]: arstn is the async active-low reset
                    to synchronise, clk is the destination clock domain. The
                    output (active-low resetn) asserts asynchronously with arstn
                    and is RELEASED synchronously to clk through STAGES flops.
                    Used to re-synchronise a CONSUMED external reset tree (e.g.
                    eth-ss CLK_RST_CONSUMER mode off CPU1's PRMU) into the
                    subsystem's own clock domain. Optional `width` carries the
                    STAGES depth (default 3).
    """
    name: str
    type: str  # passthrough, or_reduce, or_combine, and_gate, constant, concat, mux2
    output: str  # target signal (may include bit slice)
    inputs: List[str] = field(default_factory=list)  # for or_combine, and_gate, mux2 ([in0, in1])
    input: Optional[str] = None  # for passthrough, or_reduce (single input)
    value: Any = None  # for constant
    width: Optional[int] = None  # for constant (explicit width)
    concat_inputs: List[Any] = field(default_factory=list)  # for concat: raw [{bit, signal}] list
    pad_zero_above: Optional[int] = None  # for concat: bits >= this are 1'b0
    select: Optional[str] = None  # for mux2: select expression (e.g. a $PARAM ref)
    desc: str = ''


@dataclass
class InterconnectTarget:
    """A target (subordinate) in an interconnect."""
    name: str
    instance: Optional[str] = None  # associated module instance name
    base: int = 0
    size: int = 0
    phys_size: Any = None  # int or expression string
    sw_access: str = 'rw'
    region_type: Optional[str] = None
    role: Optional[str] = None
    subordinate_bus: bool = False  # True if this target connects to another bus matrix/NoC
    protocol: str = 'ahb'  # 'ahb' or 'apb'
    apb_config: Optional[Dict[str, Any]] = None  # APB bridge/mux config when protocol='apb'
    passthrough: Optional[str] = None  # module-level AHB interface name for passthrough targets
    probe: bool = False  # attach a passive ahb_perf_probe to this target's matrix port
    # A target may name a register_map the same way an AddressDecodeSlot does —
    # e.g. a gen:False peripheral (a rendered DMA-250) reached directly as an
    # interconnect target rather than through an APB slot. Resolving it here
    # puts the peripheral's registers into the generated model (RDL, discovery,
    # docs) instead of dropping the key silently.
    register_map: Optional[str] = None
    resolved_register_map: Optional['RegisterMap'] = None
    desc: str = ''


@dataclass
class InterconnectInitiatorTarget:
    """A target reference from an initiator, with optional visibility overrides."""
    name: str
    visibility: Optional[List[Dict[str, Any]]] = None  # address window overrides


@dataclass
class InterconnectInitiator:
    """An initiator (master) in an interconnect."""
    name: str
    instance: Optional[str] = None  # associated module instance name
    passthrough: Optional[str] = None  # module-level AHB interface name for passthrough initiators
    probe: bool = False  # attach a passive ahb_perf_probe to this initiator's matrix port
    targets: List[InterconnectInitiatorTarget] = field(default_factory=list)

    @property
    def target_names(self) -> List[str]:
        """Get flat list of target names."""
        return [t.name for t in self.targets]


@dataclass
class Interconnect:
    """A bus interconnect (e.g., AHB bus matrix)."""
    name: str
    gen: bool = True
    type: str = 'ahb_lite'
    desc: str = ''
    params: Dict[str, Any] = field(default_factory=dict)
    connections: List[Connection] = field(default_factory=list)
    targets: List[InterconnectTarget] = field(default_factory=list)
    initiators: List[InterconnectInitiator] = field(default_factory=list)
    # How the interconnect core (<name>_lite) is built:
    #   'busmatrix'  Arm's cmsdk_ahb_busmatrix templates via BuildBusMatrix.pl
    #                (Academic Access collateral). The default.
    #   'cmsdk_mux'  a SoC Labs core instantiating the Quickstart
    #                cmsdk_ahb_master_mux / cmsdk_ahb_slave_mux /
    #                cmsdk_ahb_default_slave by reference; see
    #                backends/cmsdk_mux.py for the topology and its limits.
    impl: str = 'busmatrix'
    # Optional perf-probe read-out config (instance_name/target_name/base/size).
    # When present + the interconnect has probed ports, the perf-probe backend
    # injects a harness + read-out slave target. None => no perf-probe.
    perf_probes: Optional[Dict[str, Any]] = None
    # Per-interconnect target-arbiter scheme override, forwarded verbatim into
    # the ARM BuildBusMatrix XML <arbitration_scheme> tag (SoCAhbBackend).
    # None => the ahb_lite interface default ('burst': fixed priority, master
    # 0 highest, does not break a defined-length burst). Deliberately a
    # dedicated field, NOT a `params` entry: `params` is forwarded blindly as
    # literal RTL parameter overrides at this interconnect's instantiation
    # site (subsystem.py:_build_interconnect, toplevel.py) and the generated
    # busmatrix module declares no such parameter -- putting it in `params`
    # emits a bogus `.arbitration_scheme(round)` override (2026-09-30, found
    # while wiring the cpu_ss round-robin fix; see PATCHSET.md gate G/G0).
    arbitration_scheme: Optional[str] = None


@dataclass
class RegisterField:
    """A field within a register."""
    name: str
    bits: str = ''
    access: str = 'RO'
    reset_value: Any = None
    desc: str = ''


@dataclass
class Register:
    """A register within a register map."""
    name: str
    offset: int = 0
    width: int = 32
    access: str = 'RW'
    reset_value: Any = None
    desc: str = ''
    fields: List['RegisterField'] = field(default_factory=list)


@dataclass
class RegisterMap:
    """A parsed register map definition."""
    name: str
    module: str = ''
    gen: bool = False
    desc: str = ''
    address_width: int = 12
    data_width: int = 32
    registers: List[Register] = field(default_factory=list)
    source_file: str = ''


@dataclass
class AddressDecodeSlot:
    """A slot in an address decoder."""
    slot: int
    name: str
    module: str = ''
    offset: int = 0
    size: int = 0
    register_map: Optional[str] = None
    resolved_register_map: Optional[RegisterMap] = None
    desc: str = ''
    address_decode: Optional['AddressDecode'] = None  # nested decode


@dataclass
class AddressDecode:
    """An address decode hierarchy (AHB/APB slave mux)."""
    type: str = ''  # ahb_slave_mux, apb_slave_mux
    module: str = ''
    bridge: Optional[Dict[str, Any]] = None
    slots: List[AddressDecodeSlot] = field(default_factory=list)


@dataclass
class LinkerRegion:
    """A memory region in a linker profile."""
    target: str
    address_select: str = 'default'
    linker_name: Optional[str] = None
    software_access: Optional[str] = None
    size_adjust: Optional[int] = None
    phys_size: Optional[int] = None
    # A fixed ORIGIN for a CPU that is not an initiator on this SoC's buses
    # (e.g. the testbench's CMSDK debug tester: RAM at 0x2000_0000 by its own
    # RTL). When set, it replaces the address address_select resolves.
    origin: Optional[int] = None


@dataclass
class LinkerProfile:
    """A firmware linker profile."""
    name: str
    regions: List[LinkerRegion] = field(default_factory=list)


@dataclass
class Firmware:
    """Firmware configuration."""
    cpu_initiator: str = ''
    # Target CPU (e.g. "cortex-m0", "cortex-m0plus"). Emitted as
    # NanoSoC_DEFAULT_CPU into the generated *_memmap.cmake so that consumer
    # CMake projects can pick up the SoC-model's CPU choice without having to
    # set NANOSOC_CPU explicitly.
    cpu: str = ''
    linker_profiles: List[LinkerProfile] = field(default_factory=list)
    adp: Optional[Dict[str, Any]] = None
    hex_adjust: Optional[Dict[str, Any]] = None


@dataclass
class BuildInfoField:
    """A field in the build_info section."""
    name: str
    type: str = 'int32'  # int32, timestamp, git_hash, string
    value: Any = None  # User-provided value (for int32, string)
    source: Optional[str] = None  # 'tool' for tool git hash, else repo
    max_length: int = 4  # Max string length (for string type)
    desc: str = ''


@dataclass
class BuildInfo:
    """Build info metadata configuration."""
    apb_slot: int = 3
    gen: bool = True
    fields: List[BuildInfoField] = field(default_factory=list)


@dataclass
class SramEntry:
    """An SRAM instance within a module.

    addr_width and data_width may be int literals or str parameter
    references (e.g. "$RAM_ADDR_W").  Use resolve() to produce a
    concrete ResolvedSram with computed depth/size.
    """
    name: str
    addr_width: Any = 14  # address width (int or "$PARAM" str); its unit: addr_unit
    data_width: Any = 32  # data width in bits (int or "$PARAM" str)
    desc: str = ''
    # 'byte' (2**addr_width bytes) or 'word' (2**addr_width words of
    # data_width bits). Without it: a literal width is a word width (the
    # field's historical meaning), $RAM_ADDR_W is a byte width (the
    # sl_ahb_sram / sl_sram convention, HADDR[RAM_ADDR_W-1:0], as the
    # discovery size guard reads it; kept for region YAMLs written before
    # addr_unit), and any other $PARAM is refused: its unit is not guessed.
    addr_unit: Optional[str] = None

    @property
    def unit(self) -> str:
        if self.addr_unit is not None:
            return self.addr_unit
        aw = self.addr_width
        if not isinstance(aw, str):
            return 'word'
        if aw.strip().lstrip('$') == 'RAM_ADDR_W':
            return 'byte'
        raise ValueError(
            f"srams entry '{self.name}': addr_width {aw!r} has no addr_unit; say whether "
            f"it is a byte or a word address width (addr_unit: byte | word)")


SRAM_ADDR_UNITS = ('byte', 'word')


@dataclass
class ResolvedSram:
    """An SRAM entry with concrete (integer) dimensions."""
    name: str
    addr_width: int
    data_width: int
    desc: str = ''
    addr_unit: str = 'word'   # 'byte' or 'word': see SramEntry.addr_unit

    @property
    def depth(self) -> int:
        """Number of words in the SRAM."""
        if self.addr_unit == 'byte':
            return max(1, (2 ** self.addr_width) // max(1, self.data_width // 8))
        return 2 ** self.addr_width

    @property
    def size_bytes(self) -> int:
        """Total size in bytes."""
        return self.depth * (self.data_width // 8)


@dataclass
class PadCell:
    """A pad-cell template: physical cell module + ordered port->expr map.

    port_map values may contain placeholders {i}{o}{e}{z}{pad}{c} and the
    literal tokens tielo/tiehi; the backend expands them per bit.  The dict
    insertion order is load-bearing — it is the emission order of the
    instance's port connections.
    """
    module: str
    port_map: Dict[str, str] = field(default_factory=dict)


@dataclass
class PowerPad:
    """A power/ground pad instantiation spec (one row of a pin-map)."""
    cell: str
    prefix: str
    count: int = 1
    index_base: int = 0
    net: Optional[str] = None          # supply net to connect (when supply_connect)
    supply_port: Optional[str] = None  # cell's supply port name (from PadTech.power_cells)
    commented: bool = False            # emit instance commented-out (44pin VDDIO_1)
    # Per-ROW supply_connect override (precedence: row -> pin_map -> pad_tech).
    # None -> inherit the ring-level flag.  Needed by a 28-pin ring whose
    # VDDIO pads are instantiated EMPTY "( );" while VDD/VSS/VDDACC are wired.
    supply_connect: Optional[bool] = None
    # M6 .io floorplan ring placement (placeholder).  A power row expands to
    # `count` instances `<prefix>_<index_base+k>`, which the .io places across
    # the ring's four sides.  `pin_side` is an optional per-instance side list
    # (length == count) consumed ONLY by the .io emitter (SoCPadRingBackend.
    # _ring_sides); a single string applies to every instance of the row.  None
    # -> the row contributes no .io placement (derive-from-pin_side path skips).
    pin_side: Optional[Any] = None


@dataclass
class PadTech:
    """A pad technology descriptor (generic, or one from a pad pack)."""
    name: str
    flavour: str = 'asic'                              # asic | fpga | generic
    gpio_tio: int = 4
    constants: Dict[str, str] = field(
        default_factory=lambda: {'tiehi': "1'b1", 'tielo': "1'b0"})
    bidir_cell: Optional[PadCell] = None
    input_cell: Optional[PadCell] = None
    bidir_io_cell: Optional[PadCell] = None           # SWDIO variant
    output_cell: Optional[PadCell] = None             # pure-output pad (mixed I/O)
    power_cells: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # {cell:{supply_port:..}}
    supply_connect: bool = True
    # Column-align the '(' of each port connection (pad each port key to the
    # cell's longest key + 1 space).  The generic GLIB cells use this aligned
    # style (.PAD (X)/.O   (X)); other cell libraries do not (.PORT(X)).
    align_ports: bool = False
    upf: Optional[Dict[str, Any]] = None              # STRETCH: raw UPF hints


@dataclass
class PadSignal:
    """A single physical pad in a MIXED-I/O pad ring (one row of a pin-map).

    Unlike the GPIO-bus path (auto-extracted P0/P1 ifaces), a mixed pad ring
    is described by an explicit per-signal list.  Each pad names the chip-top
    net(s) it bonds to and a pad-cell *kind*:

      kind == 'input'        : 1 chip output net  -> in_net  (pad -> core)
      kind == 'output'       : 1 chip output net  -> out_net (core -> pad)
      kind == 'tristate_out' : out_net + oe_net   (drive when enabled, else Z)
      kind == 'bidir'        : in/out/oe triplet  (one pad, bus -> per-bit pads)

    The boundary PAD port name defaults to upper(name); ``pad`` overrides it.
    For width>1 the pad expands to per-bit cell instances ``uPAD_<NAME>_<NN>``.
    """
    name: str                            # logical pad name (-> default PAD port, instance stem)
    kind: str = 'input'                  # input | output | tristate_out | bidir
    width: int = 1                       # pad width in bits (1 = scalar)
    pad: Optional[str] = None            # boundary PAD port override (default upper(name))
    in_net: Optional[str] = None         # chip-top input  net (input / bidir)
    out_net: Optional[str] = None        # chip-top output net (output / tristate_out / bidir)
    oe_net: Optional[str] = None         # chip-top output-enable net (tristate_out / bidir)
    oe_polarity: str = 'active_high'     # active_high | active_low (sense of oe_net)
    pin_side: Optional[str] = None       # STRETCH ring placement hint (top/left/...)


@dataclass
class PinMap:
    """A pin-count variant mapping for a given pad tech."""
    tech: str
    pin_count: int
    variant: Optional[str] = None                      # filename token override (e.g. 'no_pads')
    body_flavour: str = 'cfg'                          # cfg | direct
    gpio_net_template: str = 'pad_gpio_port{idx}'      # or 'p{idx}'
    gpio_port_width: int = 8                           # boundary inout [w-1:0]
    has_se: bool = True
    pre_module: str = ''                               # `define POWER_PINS / `include ...
    boundary_supplies: str = 'none'                    # none | ifdef_off_ports | unconditional
    supply_ports: List[str] = field(default_factory=list)
    supply_connect: Optional[bool] = None             # overrides PadTech when set
    gpio_tio: int = 4
    trailing_comma_bug: bool = False
    internal_vssio_wire: bool = False
    extra_wires: List[str] = field(default_factory=list)
    bonded_gpio: Dict[str, List[int]] = field(default_factory=dict)   # {'P0':[0..7],...}
    tie_off: Dict[str, List[int]] = field(default_factory=dict)       # {'P0':[8..15],...}
    power_pads: List[PowerPad] = field(default_factory=list)
    ring: Optional[Dict[str, List[str]]] = None       # STRETCH: side->[inst] for *.io
    # cfg-body knobs (tech-specific structural variants on the shared cfg body):
    uart_txd_tie: bool = True                          # `wire soc_uart_txd_o = 1'b1;` vs bare
    chip_power_pins: bool = False                      # `ifdef POWER_PINS .VDD/.VSS/.VDDACC on the chip inst
    power_pins_guard: bool = False                     # wrap boundary supplies + power pads in `ifdef POWER_PINS
    # Per-control-pin REN override (the {ren} placeholder of cells with a pull-enable); keys
    # se/clk/test/nrst/swdck.  Empty -> {ren} is left to expand from the cell.
    ctrl_ren: Dict[str, str] = field(default_factory=dict)
    # Control-pad boundary direction.  Most variants declare CLK/TEST/NRST/SWDCK
    # as `input wire` (most fixed-pin rings); the generic no_pads variant declares
    # them `inout wire` (they go through generic bidir cells).  False -> input.
    ctrl_inout: bool = False
    # Instance-name override for the TEST control pad (the no_pads hand file names
    # it uPAD_XTAL_I rather than the auto-generated uPAD_TEST_I).
    test_inst_name: str = 'uPAD_TEST_I'
    # ---- MIXED-I/O mode (per-signal pad list) — additive, default-empty ----
    mode: str = 'gpio'                                 # gpio | mixed
    chip_module: Optional[str] = None                 # wrapped chip top (mixed mode)
    pads: List['PadSignal'] = field(default_factory=list)  # explicit pad list (mixed mode)


@dataclass
class ChipPad:
    """A bonded chip-boundary pad (one entry of a chip-boundary spec).

    Re-exposes inner SoC port(s) on the generated bonded chip wrapper:

      kind == 'input'        : pad consumes 1 inner input  -> in_net  (port=in)
      kind == 'output'       : pad consumes 1 inner output -> out_net (port=out)
      kind == 'tristate_out' : pad consumes out + oe       (no input back to core)
      kind == 'bidir'        : pad consumes the in/out/oe triplet (one pad)

    The pad is exposed on the chip as its constituent inner net(s) 1:1 (same
    names/widths/directions as the inner SoC); the only true `inout` lives in
    the downstream pad ring, which consumes these in/out/oe ports.
    """
    chip_port: str                       # logical pad name (group label)
    kind: str = 'input'                  # input | output | tristate_out | bidir
    width: int = 1                       # pad width in bits (1 = scalar)
    in_net: Optional[str] = None         # inner input  port (input / bidir)
    out_net: Optional[str] = None        # inner output port (output / tristate_out / bidir)
    oe_net: Optional[str] = None         # inner output-enable port (tristate_out / bidir)
    oe_polarity: str = 'active_high'     # active_high | active_low (sense of oe_net)
    group: str = ''                      # logical group (clk/swd/qspi/...) for comments


@dataclass
class ChipTerminator:
    """A helper module instantiated inside a wrapper to terminate a bus port.

    Some inner SoC ports cannot be classified as a bonded pad, a constant tie
    or an open: an AHB manager port with no slave attached needs a *responder*,
    not a constant.  Tying its `hready` high makes the window read as zero and
    swallow writes; leaving it unconnected makes synthesis tie it low and wedge
    the bus matrix.  Either way the fault behaviour of an unmapped region is
    silently lost.

    A terminator declares the module to instantiate and how its ports attach:

      soc_ports:   {term_port: inner_soc_port}
          A wire named after the inner SoC port is declared in the wrapper and
          connects the terminator to `u_soc`.

      widths:      {inner_soc_port: int}
          Wire widths, for the same reason `pads` declare `width`: a wrapper
          does not instantiate the inner SoC's parameters, so it cannot write
          `wire [SYS_DATA_W-1:0]`.  Omitted entries default to 1 bit.  When the
          SoC model is available the two are cross-checked.

      bonded_refs: {term_port: inner_soc_port}
          The terminator reads a net the wrapper *already* has for a bonded
          inner port (a clock, a reset).  Each backend substitutes whatever net
          it actually used for that port, so FPGA `port_renames` are honoured.

    Only `soc_ports` entries are counted as *classified* by `coverage()`;
    `bonded_refs` merely observe ports classified elsewhere.
    """
    module: str                                          # e.g. nanosoc_d2d_idle_slave
    instance: str                                        # e.g. u_d2d_idle_slave
    params: Dict[str, Any] = field(default_factory=dict)
    soc_ports: Dict[str, str] = field(default_factory=dict)
    widths: Dict[str, int] = field(default_factory=dict)
    bonded_refs: Dict[str, str] = field(default_factory=dict)
    desc: str = ''


@dataclass
class ChipBoundary:
    """A chip-boundary spec: how an inner SoC top is wrapped into a bonded chip.

    Loaded from lib/chip_boundary/<name>.yaml.  Classifies every inner SoC
    port as exactly one of: a bonded pad (`pads`), a tied input (`ties`,
    driven to a constant inside the wrapper), an open output (`opens`, left
    unconnected), or a port driven/read by a `terminators` helper instance.
    These are user-reviewable functional defaults — the template never
    hard-codes ties/opens.
    """
    chip_module: str                                     # generated wrapper name
    inner_module: str                                    # inner SoC top wrapped
    pads: List[ChipPad] = field(default_factory=list)
    ties: List[Dict[str, str]] = field(default_factory=list)   # [{soc_port, const}]
    opens: List[str] = field(default_factory=list)             # inner output names
    terminators: List[ChipTerminator] = field(default_factory=list)


@dataclass
class FpgaProfile:
    """The BESPOKE FPGA/board layer for the FPGA-wrapper backend (doc-07 M4).

    Loaded from lib/fpga_profile/<name>.yaml.  The model-derivable topology
    (which inner SoC port is bonded / tied / open, plus each bidir pad's oe
    polarity) lives in the ChipBoundary; this descriptor carries only the bits
    that CANNOT be derived from the model:

      * board pin renames (inner net name -> Vivado/board port name),
      * FPGA parameter overrides (Zynq fit: RAM_ADDR_W shrinks, $readmemh img),
      * debug taps (extra outputs with no boundary equivalent),
      * dropped pads (hostio4, JTAG, scan/test, rtc_clk),
      * bespoke feature glue (SYSRESETREQ self-loop, rtc_clk<-sys_fclk, ...),
      * the IP-XACT consistency contract (curated physical_name set + buses).

    Every field maps 1:1 to a residual in the backend report — none of it is
    auto-derived.  All fields default empty so an absent/partial profile is
    inert (the wrapper degrades to a straight tristate emission).
    """
    name: str = ''
    module_name: str = ''                                       # FPGA wrapper top
    tristate_convention: str = 'vivado'                         # vivado (_i/_o/_t)
    param_overrides: List[Dict[str, str]] = field(default_factory=list)
    port_renames: Dict[str, str] = field(default_factory=dict)  # inner_net -> port
    exclude_pads: List[str] = field(default_factory=list)       # boundary chip_ports
    exclude_ties: Dict[str, str] = field(default_factory=dict)  # inner_in -> const
    debug_taps: List[Dict[str, str]] = field(default_factory=list)
    features: Dict[str, Any] = field(default_factory=dict)
    ip_xact: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Instance:
    """An instance of a module within a parent module."""
    instance_name: str
    module_name: str = ''  # YAML module name (from module: or rtl_module:)
    is_rtl_module: bool = False  # True if rtl_module: (no YAML file)
    addressable: bool = False
    condition: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    connections: List[Connection] = field(default_factory=list)
    inline_interfaces: List[Interface] = field(default_factory=list)  # for rtl_module
    resolved_module: Optional['Module'] = None  # set by builder


@dataclass
class Module:
    """A hardware module (top-level or sub-module)."""
    name: str
    gen: bool = False
    desc: str = ''
    source_file: str = ''

    params: Dict[str, Param] = field(default_factory=dict)
    clocks: List[Clock] = field(default_factory=list)
    resets: List[Reset] = field(default_factory=list)
    interfaces: List[Interface] = field(default_factory=list)
    instances: List[Instance] = field(default_factory=list)
    assigns: List[Assign] = field(default_factory=list)
    glue_logic: List[GlueLogicEntry] = field(default_factory=list)
    internal_wires: List[Interface] = field(default_factory=list)
    interconnects: List[Interconnect] = field(default_factory=list)
    address_decode: Optional[AddressDecode] = None
    firmware: Optional[Firmware] = None
    build_info: Optional[BuildInfo] = None
    srams: List[SramEntry] = field(default_factory=list)
    constraints: Optional['ConstraintSet'] = None

    def get_interface(self, name: str) -> Optional[Interface]:
        """Find an interface by name."""
        for iface in self.interfaces:
            return next((i for i in self.interfaces if i.name == name), None)
        return None

    def get_instance(self, name: str) -> Optional[Instance]:
        """Find an instance by name."""
        return next((i for i in self.instances if i.instance_name == name), None)

    def get_interconnect(self, name: str) -> Optional[Interconnect]:
        """Find an interconnect by name."""
        return next((ic for ic in self.interconnects if ic.name == name), None)

    @property
    def flat_params(self) -> Dict[str, Any]:
        """Get params as {name: default_value} dict."""
        return {name: p.default for name, p in self.params.items()}
