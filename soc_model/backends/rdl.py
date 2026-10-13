"""RDL backend — generates SystemRDL register descriptions and optionally RTL.

Walks the module hierarchy collecting all RegisterMap objects from
AddressDecodeSlot entries. For each register map:
  - Generates a SystemRDL (.rdl) file describing the register block
  - If the register map has gen: True, compiles the RDL and generates
    RTL (SystemVerilog) using peakrdl-regblock

Generated files per register map:
  - <name>.rdl                  — SystemRDL register description
  - rtl/<name>_pkg.sv           — (gen only) generated RTL package
  - rtl/<name>.sv               — (gen only) generated RTL register block
"""

from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from ..model import (
    AddressDecode, AddressDecodeSlot, Module, Register, RegisterField,
    RegisterMap,
)
from ..utils import write_if_changed

# Access type mapping: YAML access -> SystemRDL sw property
_ACCESS_MAP = {
    'RW':  'rw',
    'RO':  'r',
    'WO':  'w',
    'W1C': 'rw',  # W1C is modelled as rw with onwrite = woclr
    'RC':  'r',   # Read-clear: sw=r with onread = rclr
}


def _rdl_access(access: str) -> str:
    """Map YAML access type to SystemRDL sw= property value."""
    return _ACCESS_MAP.get(access.upper(), 'rw')


def _needs_onwrite_woclr(access: str) -> bool:
    """Check if the access type needs onwrite = woclr (write-1-to-clear)."""
    return access.upper() == 'W1C'


def _needs_onread_rclr(access: str) -> bool:
    """Check if the access type needs onread = rclr (read-clear)."""
    return access.upper() == 'RC'


def _parse_bits(bits_str: str) -> Tuple[int, int]:
    """Parse a bit range string like '7:0' or '3' into (high, low)."""
    if ':' in bits_str:
        parts = bits_str.split(':')
        return int(parts[0]), int(parts[1])
    bit = int(bits_str)
    return bit, bit


def _sanitize_name(name: str) -> str:
    """Sanitize a name for use as a SystemRDL identifier."""
    # Replace any non-alphanumeric/underscore characters
    sanitized = ''.join(c if c.isalnum() or c == '_' else '_' for c in name)
    # Ensure it doesn't start with a digit
    if sanitized and sanitized[0].isdigit():
        sanitized = f'_{sanitized}'
    return sanitized


def _format_reset_value(value, width: int = 32) -> str:
    """Format a reset value as a SystemRDL-compatible literal."""
    if value is None:
        return "0"
    if isinstance(value, int):
        return f"{width}'h{value:x}"
    if isinstance(value, str):
        # Handle hex strings like "0x00000000"
        try:
            v = int(value, 0)
            return f"{width}'h{v:x}"
        except ValueError:
            return "0"
    return "0"


class SoCRdlBackend:
    """Generates SystemRDL files and optionally RTL from the SoC model."""

    def __init__(self, top_module: Module):
        self.top = top_module

    def generate_all(self, build_dir: Path) -> List[Tuple[str, Path, bool]]:
        """Generate RDL for all register maps found in the module hierarchy.

        RDL files go to build_dir/rdl/.
        Generated RTL goes to build_dir/rtl/<regmap_name>/.

        Returns list of (regmap_name, output_path, rtl_generated) tuples.
        """
        generated = []
        seen: Set[str] = set()

        # Collect all register maps from the hierarchy
        register_maps = self._collect_register_maps(self.top, seen)

        if not register_maps:
            return generated

        rdl_dir = build_dir / 'rdl'
        rdl_dir.mkdir(parents=True, exist_ok=True)

        for rm in register_maps:
            rdl_path = rdl_dir / f"{rm.name}.rdl"

            # Generate the RDL file
            rdl_content = self._generate_rdl(rm)
            write_if_changed(rdl_path, rdl_content)
            print(f"  RDL: {rdl_path}")

            rtl_generated = False

            # If gen: True, generate RTL from the RDL into build_dir/rtl/<name>/
            if rm.gen:
                rtl_out = build_dir / 'rtl' / rm.name
                rtl_generated = self._generate_rtl(rdl_path, rm, rtl_out)

            generated.append((rm.name, rdl_path, rtl_generated))

        return generated

    def generate_single(self, rm: RegisterMap,
                        build_dir: Path) -> Tuple[Path, bool]:
        """Generate RDL (and optionally RTL) for a single RegisterMap.

        RDL files go to build_dir/rdl/.
        Generated RTL goes to build_dir/rtl/<regmap_name>/.

        Returns (rdl_path, rtl_generated).
        """
        rdl_dir = build_dir / 'rdl'
        rdl_dir.mkdir(parents=True, exist_ok=True)

        rdl_path = rdl_dir / f"{rm.name}.rdl"
        rdl_content = self._generate_rdl(rm)
        write_if_changed(rdl_path, rdl_content)

        rtl_generated = False
        if rm.gen:
            rtl_out = build_dir / 'rtl' / rm.name
            rtl_generated = self._generate_rtl(rdl_path, rm, rtl_out)

        return rdl_path, rtl_generated

    # -------------------------------------------------------------------
    # Register map collection
    # -------------------------------------------------------------------

    def _collect_register_maps(self, module: Module,
                               seen: Set[str]) -> List[RegisterMap]:
        """Recursively collect all RegisterMap objects from the module hierarchy."""
        results = []

        # Check address_decode slots
        if module.address_decode:
            self._collect_from_address_decode(module.address_decode, seen, results)

        # Recurse into child instances
        for inst in module.instances:
            if inst.resolved_module:
                results.extend(
                    self._collect_register_maps(inst.resolved_module, seen)
                )

        return results

    def _collect_from_address_decode(self, ad: AddressDecode,
                                     seen: Set[str],
                                     results: List[RegisterMap]):
        """Collect register maps from an address decode hierarchy."""
        for slot in ad.slots:
            if slot.resolved_register_map and slot.resolved_register_map.name not in seen:
                seen.add(slot.resolved_register_map.name)
                results.append(slot.resolved_register_map)
            # Recurse into nested address decodes
            if slot.address_decode:
                self._collect_from_address_decode(slot.address_decode, seen, results)

    # -------------------------------------------------------------------
    # RDL generation
    # -------------------------------------------------------------------

    def _generate_rdl(self, rm: RegisterMap) -> str:
        """Generate SystemRDL content for a single register map."""
        lines = []

        # File header
        lines.append(f'// Auto-generated SystemRDL for {rm.name}')
        lines.append(f'// Source: {rm.source_file}')
        if rm.desc:
            lines.append(f'// {rm.desc.strip()}')
        lines.append('')

        # Address map containing the register block
        addrmap_name = _sanitize_name(rm.name)
        regblock_name = _sanitize_name(f'{rm.name}_regs')

        # Generate the regfile type with all registers
        lines.append(f'regfile {regblock_name} {{')
        lines.append(f'    default hw = r;')
        lines.append('')

        for reg in rm.registers:
            lines.extend(self._generate_register(reg, rm.data_width))
            lines.append('')

        lines.append(f'}};')
        lines.append('')

        # Generate the addrmap that instantiates the regfile
        lines.append(f'addrmap {addrmap_name} {{')
        lines.append(f'    default accesswidth = {rm.data_width};')
        lines.append(f'    {regblock_name} regs;')
        lines.append(f'}};')
        lines.append('')

        return '\n'.join(lines)

    def _generate_register(self, reg: Register, data_width: int) -> List[str]:
        """Generate SystemRDL lines for a single register."""
        lines = []
        reg_name = _sanitize_name(reg.name)

        lines.append(f'    reg {reg_name}_t {{')

        # Description as a property inside the body
        if reg.desc:
            escaped_desc = reg.desc.strip().replace('"', '\\"')
            lines.append(f'        desc = "{escaped_desc}";')

        if reg.fields:
            for fld in reg.fields:
                lines.extend(self._generate_field(fld, reg, data_width))
        else:
            # Simple register without field decomposition — single DATA field
            sw = _rdl_access(reg.access)
            lines.append(f'        field {{')
            lines.append(f'            sw = {sw};')
            if _needs_onwrite_woclr(reg.access):
                lines.append(f'            onwrite = woclr;')
            if _needs_onread_rclr(reg.access):
                lines.append(f'            onread = rclr;')
            if reg.reset_value is not None:
                lines.append(f'            reset = {_format_reset_value(reg.reset_value, reg.width)};')
            lines.append(f'        }} DATA[{reg.width}];')

        lines.append(f'    }};')

        # Instantiate the register at its offset
        lines.append(f'    {reg_name}_t {reg_name} @0x{reg.offset:03X};')

        return lines

    def _generate_field(self, fld: RegisterField, reg: Register,
                        data_width: int) -> List[str]:
        """Generate SystemRDL lines for a single field within a register."""
        lines = []
        fld_name = _sanitize_name(fld.name)
        sw = _rdl_access(fld.access)

        # Determine bit width
        high, low = _parse_bits(fld.bits)
        width = high - low + 1

        lines.append(f'        field {{')
        lines.append(f'            sw = {sw};')

        if _needs_onwrite_woclr(fld.access):
            lines.append(f'            onwrite = woclr;')
        if _needs_onread_rclr(fld.access):
            lines.append(f'            onread = rclr;')

        if fld.reset_value is not None:
            lines.append(f'            reset = {_format_reset_value(fld.reset_value, width)};')

        if fld.desc:
            escaped_desc = fld.desc.strip().replace('"', '\\"')
            lines.append(f'            desc = "{escaped_desc}";')

        # Explicit position (GEN-BACKLOG 22): `NAME[width]` lets SystemRDL pack
        # fields from bit 0 in declaration order, so a gap (dualtimer Control
        # bit 4) or a high-to-low field list (rom table PIDs) moved fields
        # silently. `NAME[high:low]` places each field at its YAML `bits:`.
        lines.append(f'        }} {fld_name}[{high}:{low}];')

        return lines

    # -------------------------------------------------------------------
    # RTL generation (via peakrdl-regblock)
    # -------------------------------------------------------------------

    def _generate_rtl(self, rdl_path: Path, rm: RegisterMap,
                      output_dir: Path) -> bool:
        """Compile RDL and generate RTL using peakrdl-regblock.

        Returns True if RTL was generated successfully.
        """
        try:
            from systemrdl import RDLCompiler
            from peakrdl_regblock import RegblockExporter
            from peakrdl_regblock.udps import ALL_UDPS
            from peakrdl_regblock.cpuif.apb4 import APB4_Cpuif_flattened as APB4_Cpuif
        except ImportError:
            print(f"  WARNING: systemrdl-compiler or peakrdl-regblock not installed. "
                  f"Skipping RTL generation for {rm.name}. "
                  f"Install with: pip install systemrdl-compiler peakrdl-regblock")
            return False

        try:
            # Compile the RDL with peakrdl-regblock UDPs registered
            rdlc = RDLCompiler()
            for udp in ALL_UDPS:
                rdlc.register_udp(udp)
            rdlc.compile_file(str(rdl_path))
            root = rdlc.elaborate()

            # Export RTL with APB4 CPU interface
            output_dir.mkdir(parents=True, exist_ok=True)
            exporter = RegblockExporter()
            exporter.export(
                root,
                str(output_dir),
                cpuif_cls=APB4_Cpuif,
            )

            # Generate APB wrapper with standard port names.
            # The regblock's own s_apb_paddr width is decided by peakrdl-regblock
            # from the register content, NOT by rm.address_width (which describes
            # the APB *window* the region decodes). Pass the exporter's computed
            # width through so the instance connection matches the port exactly.
            self._generate_apb_wrapper(
                rm, output_dir, regblock_addr_width=exporter.ds.addr_width)

            print(f"  RTL generated: {output_dir}")
            return True

        except Exception as e:
            print(f"  ERROR generating RTL for {rm.name}: {e}")
            return False

    def _generate_apb_wrapper(self, rm: RegisterMap, output_dir: Path,
                              regblock_addr_width: int = None):
        """Generate an APB wrapper that bridges standard APB ports to PeakRDL flattened ports.

        The wrapper maps standard APB signal names (PCLK, PRESETn, PSEL, etc.)
        to the PeakRDL-regblock flattened names (clk, rst, s_apb_psel, etc.)
        and ties off unused inputs (pprot, pstrb).

        Two distinct address widths are in play and must not be conflated:

        * ``rm.address_width`` -- the APB *window* this region occupies. It sets
          the wrapper's external PADDR port, which the parent region drives at
          its full width (e.g. exp_paddr[11:0]).
        * ``regblock_addr_width`` -- what peakrdl-regblock actually declared for
          ``s_apb_paddr``, derived from the register content (e.g. 8 bits for a
          256-byte table). It sets the *instance connection* slice.

        Driving the window width into the regblock port is a hard width mismatch:
        DC raises LINK-3 and black-boxes the regblock rather than truncating.
        """
        regblock_name = rm.name
        wrapper_name = f'{regblock_name}_apb_wrapper'
        addr_width = rm.address_width

        # Fall back to the window width only when the regblock width is unknown
        # (e.g. a direct call that never ran the exporter).
        rb_addr_width = (addr_width if regblock_addr_width is None
                         else regblock_addr_width)

        if rb_addr_width <= addr_width:
            paddr_conn = f'PADDR[{rb_addr_width-1}:0]'
        else:
            # Regblock needs more address bits than the window supplies: keep the
            # connection width-correct by zero-extending rather than emitting an
            # out-of-range part-select.
            paddr_conn = f"{{{{{rb_addr_width-addr_width}{{1'b0}}}}, PADDR}}"

        wrapper = f"""\
//-----------------------------------------------------------------------------
// {wrapper_name} — APB wrapper for PeakRDL-generated {regblock_name}
//-----------------------------------------------------------------------------
// AUTO-GENERATED FILE - DO NOT EDIT DIRECTLY.
// Bridges standard APB interface to PeakRDL-regblock flattened APB4 ports.
//-----------------------------------------------------------------------------

module {wrapper_name} (
    input  wire        PCLK,
    input  wire        PRESETn,
    input  wire        PSEL,
    input  wire        PENABLE,
    input  wire        PWRITE,
    input  wire [{addr_width-1}:0] PADDR,
    input  wire [31:0] PWDATA,
    output wire [31:0] PRDATA,
    output wire        PREADY,
    output wire        PSLVERR
);

    {regblock_name} u_{regblock_name} (
        .clk            (PCLK),
        .rst            (~PRESETn),

        .s_apb_psel     (PSEL),
        .s_apb_penable  (PENABLE),
        .s_apb_pwrite   (PWRITE),
        .s_apb_pprot    (3'b000),
        .s_apb_paddr    ({paddr_conn}),
        .s_apb_pwdata   (PWDATA),
        .s_apb_pstrb    (4'b1111),
        .s_apb_pready   (PREADY),
        .s_apb_prdata   (PRDATA),
        .s_apb_pslverr  (PSLVERR),

        .hwif_out       ()
    );

endmodule
"""
        wrapper_path = output_dir / f'{wrapper_name}.sv'
        write_if_changed(wrapper_path, wrapper)
