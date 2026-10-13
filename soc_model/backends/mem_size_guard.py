# Copyright 2026, SoC Labs (www.soclabs.org)
"""Discovery size guard: the size a discovery table reports for a memory is
the size of the memory actually compiled behind it.

Every memory size in a nanoSoC exists twice: once as hardware (an address
width passed down to a RAM or a boot ROM) and once as the `phys_size`
expression of the interconnect target, which the discovery table
(TGT_i_SIZE) and the linker MEMORY blocks are written from. Nothing compared
the two, and they diverged in both directions:

  * RAMs. sl_ahb_sram / sl_sram slice HADDR[RAM_ADDR_W-1:0]: RAM_ADDR_W is a
    BYTE address width, 2**RAM_ADDR_W bytes. YAML that said
    "4 * (2 ** $..._RAM_ADDR_W)" over-reported every RAM 4x (arch_tech before
    72500d9; eth rc4 and nanosoc_m0_soc still carry it), and a generator that
    resolved a subsystem's default instead of the top's override made it 8x.
  * Boot ROMs. The unit of ROM_ADDR_W depends on the WRAPPER. The hand-written
    wrappers (arch_tech, compute_ss, the multicore's CPU1, eth_ss) slice
    HADDR[ROM_ADDR_W-1:2] into a word_addr port: a BYTE width, 2**N bytes.
    bootrom_gen.py's generated wrapper (the Quickstart's) slices
    HADDR[ROM_ADDR_W+1:2]: a WORD width, 4 * 2**N bytes. No unit rule is
    right for both, so a ROM is never sized by rule: its size is read from
    the wrapper's actual slice and the ROM's word_addr width.

What this check does, after the render:

  1. Reads every rendered discovery table (build_soc/discovery/*.yaml).
  2. Walks the model to each memory target (region_type memory) of every
     generated interconnect and finds the instance behind it, then evaluates
     that instance's parameters from the RENDERED RTL, top down: the top's
     defaults (plus --config-override, as the chip passes them), and each
     instantiation's #(.P(expr)) in its parent's scope. The values are the
     ones the compiled design elaborates, not the YAML's.
  3. Derives the real size from the instance's module:
       * its source is known (rendered, or listed in an --rtl-flist): the
         module is searched, recursively, for the memory itself --
         sl_ahb_sram (RAM_ADDR_W) or sl_sram (AW): 2**W bytes; an instance
         whose `word_addr` port takes HADDR[hi:lo] (lo must be 2): the decode
         reaches 2**(hi-lo+1) words, the ROM module holds 2**(its word_addr
         width) words, and the memory is 4 * 2**min(the two) bytes (a deeper
         ROM is partly unreachable, a shallower one repeats);
       * its source is not known: a module with a RAM_ADDR_W parameter is a
         RAM region (2**RAM_ADDR_W bytes, the sl_ahb_sram convention).
  4. Gives every memory target one verdict:
       OK          the table equals the compiled memory;
       MISMATCH    it does not                                  -> the run FAILS;
       UNRESOLVED  nothing sizes it: a boot ROM whose wrapper is not in the
                   compile list (the unit is the wrapper's, so no rule may
                   stand in), a target with no instance, an off-chip window
                   (QSPI XIP: no RTL sizes the flash)            -> the run FAILS;
       WINDOW      a window onto a generated subsystem, whose own memories are
                   checked at its own interconnect (not a memory itself): the
                   instance CONNECTED to the target is that subsystem, and the
                   target's `instance:`, if any, names it. An `instance:` that
                   names another instance than the connected one, or no
                   instance, is UNRESOLVED                       -> the run FAILS;
       WAIVED      named by --mem-size-waive <ic>.<target>=<reason>; the line
                   still says what the guard found, and the summary lists
                   every waiver with its reason. A waiver on a target that
                   would pass (OK or WINDOW) is stale     -> the run FAILS.
     Fail-closed: a size that cannot be established is an error, never a
     pass. A consumer fixes UNRESOLVED with ONE argument to its soc_model
     call: --rtl-flist <the file list its build compiles>, or, for a target
     no RTL can size, --mem-size-waive <ic>.<target>=<why>.

Why not find the wrapper without a file list: module names are reused across
repositories (nanosoc_region_bootrom has a hand-written byte-slice form in
arch_tech, a byte-slice override in the multicore and in compute, and
bootrom_gen.py's word-slice form, generated only by the firmware build), and
consumer trees hold stale copies (FPGA project ipshared/, imp/, firmware
outputs of other images). Only the compile list says which one is built.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import yaml

from .hsel_lint import _balanced, _split_top, _strip_comments

_ID = r'[A-Za-z_][A-Za-z0-9_$]*'
_MODULE_RE = re.compile(r'\bmodule\s+(' + _ID + r')')
_ENDMODULE_RE = re.compile(r'\bendmodule\b')

# The memory primitives a size is read from: module -> its byte-width parameter.
RAM_PRIMITIVES = {'sl_ahb_sram': 'RAM_ADDR_W', 'sl_sram': 'AW'}
WORD_BYTES = 4

FIX_ADVICE = (
    "phys_size must be the size of the memory the target decodes to: for a RAM "
    "region 2 ** RAM_ADDR_W (a BYTE width), for a boot ROM whatever ITS "
    "wrapper decodes (2 ** N behind a HADDR[N-1:2] slice, 4 * 2 ** N behind "
    "HADDR[N+1:2]). Fix the YAML formula or the address width, not this check.")


# ---------------------------------------------------------------------------
# Constant expressions (Verilog subset: what parameter overrides use)
# ---------------------------------------------------------------------------
_TOK_RE = re.compile(r"\s*(?:(?P<num>(?:\d[\d_]*)?\s*'\s*[sS]?[bBoOdDhH]\s*[0-9a-fA-F_]+|\d[\d_]*)"
                     r"|(?P<id>\$?" + _ID + r")|(?P<op>\*\*|<<|>>|[-+*/%()~&|^\[\]:,?]))")


class _NotConstant(Exception):
    pass


def _num(text: str) -> int:
    t = text.replace('_', '').replace(' ', '')
    if "'" not in t:
        return int(t)
    digits = t.split("'", 1)[1].lstrip('sS')
    base = {'b': 2, 'o': 8, 'd': 10, 'h': 16}[digits[0].lower()]
    if re.search(r'[xXzZ?]', digits[1:]):
        raise _NotConstant(text)
    return int(digits[1:], base)


def eval_const(expr: str, env: Dict[str, Optional[int]]) -> int:
    """Evaluate a constant integer expression; _NotConstant if it is not one
    (an unknown name, a string, an x/z literal, a construct not handled)."""
    toks, pos, text = [], 0, expr.strip()
    while pos < len(text):
        m = _TOK_RE.match(text, pos)
        if not m or m.end() == pos:
            raise _NotConstant(expr)
        pos = m.end()
        for kind in ('num', 'id', 'op'):
            if m.group(kind) is not None:
                toks.append((kind, m.group(kind)))
                break
    i = [0]

    def peek():
        return toks[i[0]] if i[0] < len(toks) else (None, None)

    def take(val=None):
        k, v = peek()
        if k is None or (val is not None and v != val):
            raise _NotConstant(expr)
        i[0] += 1
        return k, v

    def primary():
        k, v = take()
        if k == 'num':
            return _num(v)
        if k == 'id':
            if v == '$clog2':
                take('(')
                x = ternary()
                take(')')
                return max(0, (x - 1).bit_length())
            name = v.lstrip('$')
            if env.get(name) is None:
                raise _NotConstant(name)
            val = env[name]
            if peek()[1] == '[':                     # bit- or part-select
                take('[')
                hi = ternary()
                lo = hi
                if peek()[1] == ':':
                    take(':')
                    lo = ternary()
                take(']')
                return (val >> lo) & ((1 << (hi - lo + 1)) - 1)
            return val
        if v == '(':
            x = ternary()
            take(')')
            return x
        if v in ('-', '+', '~'):
            x = unary_operand()
            return -x if v == '-' else (x if v == '+' else ~x)
        raise _NotConstant(expr)

    def unary_operand():
        return power()

    def power():
        b = primary()
        if peek()[1] == '**':
            take('**')
            return b ** power()
        return b

    def binary(level):
        ops = [('|',), ('^',), ('&',), ('<<', '>>'), ('+', '-'), ('*', '/', '%')]
        if level == len(ops):
            return power()
        x = binary(level + 1)
        while peek()[1] in ops[level]:
            _, op = take()
            y = binary(level + 1)
            x = {'|': lambda a, b: a | b, '^': lambda a, b: a ^ b, '&': lambda a, b: a & b,
                 '<<': lambda a, b: a << b, '>>': lambda a, b: a >> b,
                 '+': lambda a, b: a + b, '-': lambda a, b: a - b,
                 '*': lambda a, b: a * b, '/': lambda a, b: a // b,
                 '%': lambda a, b: a % b}[op](x, y)
        return x

    def ternary():
        c = binary(0)
        if peek()[1] == '?':
            take('?')
            a = ternary()
            take(':')
            b = ternary()
            return a if c else b
        return c

    out = ternary()
    if i[0] != len(toks):
        raise _NotConstant(expr)
    return out


def try_const(expr: Optional[str], env) -> Optional[int]:
    if expr is None:
        return None
    try:
        return eval_const(expr, env)
    except (_NotConstant, ValueError, ZeroDivisionError, KeyError, IndexError, OverflowError):
        return None


# ---------------------------------------------------------------------------
# RTL: modules, their parameters, instances and ports
# ---------------------------------------------------------------------------
@dataclass
class RtlInstance:
    module: str
    name: str
    params: List[Tuple[str, str]]          # (name, expr) of #(.P(expr)); positional: ('', expr)
    conns: Dict[str, str]                  # port -> expr


@dataclass
class RtlModule:
    name: str
    path: Path
    params: List[Tuple[str, str]] = field(default_factory=list)   # header + body, in order
    ports: Dict[str, str] = field(default_factory=dict)           # port -> range text ('' if none)
    instances: List[RtlInstance] = field(default_factory=list)
    assigns: Dict[str, str] = field(default_factory=dict)         # plain net -> rhs

    def env(self, overrides: Dict[str, int]) -> Dict[str, Optional[int]]:
        """Parameter values: header/body defaults in order, each evaluated in
        the scope built so far; an override replaces a default."""
        env: Dict[str, Optional[int]] = {}
        for name, expr in self.params:
            env[name] = overrides[name] if name in overrides else try_const(expr, env)
        for k, v in overrides.items():
            env.setdefault(k, v)
        return env


_PARAM_DECL_RE = re.compile(
    r'^\s*(?:parameter|localparam)\b(?:\s+(?:int|integer|logic|bit|reg|signed|unsigned|'
    r'longint|shortint|byte)\b)*(?:\s*\[[^\]]*\])?\s*(.*)$', re.S)


def _params_from(text: str) -> List[Tuple[str, str]]:
    """`[parameter] [type] [range] A = e1, B = e2` -> [(A, e1), (B, e2)]."""
    out = []
    m = _PARAM_DECL_RE.match(text)
    body = m.group(1) if m else text
    for item in _split_top(body, ','):
        item = item.strip()
        mm = _PARAM_DECL_RE.match(item)
        if mm:
            item = mm.group(1).strip()
        am = re.match(r'^(' + _ID + r')\s*=\s*(.+)$', item, re.S)
        if am:
            out.append((am.group(1), am.group(2).strip()))
    return out


def _ports_from(text: str, ports: Dict[str, str]):
    for item in _split_top(text, ','):
        m = re.match(r'^\s*(input|output|inout)\b(.*)$', item, re.S)
        if not m:
            continue
        rest = m.group(2)
        rng = re.search(r'\[([^\]]*)\]', rest)
        names = re.findall(_ID, re.sub(r'\[[^\]]*\]', ' ', rest))
        names = [n for n in names if n not in (
            'wire', 'logic', 'reg', 'signed', 'unsigned', 'var', 'tri')]
        for n in names:
            ports[n] = rng.group(1) if rng else ''


def parse_module(name: str, chunk: str, path: Path) -> RtlModule:
    mod = RtlModule(name=name, path=path)
    m = _MODULE_RE.search(chunk)
    rest = chunk[m.end():]
    j = 0
    mm = re.match(r'\s*#\s*\(', rest)
    if mm:
        j = _balanced(rest, mm.end() - 1)
        mod.params += _params_from(rest[mm.end():j - 1])
    mp = re.match(r'\s*\(', rest[j:])
    body = rest[j:]
    if mp:
        k = j + mp.end() - 1
        end = _balanced(rest, k)
        _ports_from(rest[k + 1:end - 1], mod.ports)
        body = rest[end:]
    body = body.split(';', 1)[1] if ';' in body else body
    body = _ENDMODULE_RE.split(body)[0]
    for stmt in _split_top(body, ';'):
        s = re.sub(r'^\s*(?:(?:generate|endgenerate|begin|end|else)\b\s*(?::\s*' + _ID + r')?\s*)+', '', stmt)
        s = re.sub(r'^\s*if\s*\(.*?\)\s*begin\s*(?::\s*' + _ID + r')?', '', s, flags=re.S).strip()
        if not s:
            continue
        if re.match(r'^(parameter|localparam)\b', s):
            mod.params += _params_from(s)
            continue
        if re.match(r'^(input|output|inout)\b', s):
            _ports_from(s, mod.ports)
            continue
        am = re.match(r'^assign\s+(' + _ID + r')\s*=(?!=)\s*(.*)$', s, re.S)
        if am:
            mod.assigns[am.group(1)] = am.group(2).strip()
            continue
        dm = re.match(r'^(?:wire|logic)\b[^=]*?(' + _ID + r')\s*=(?!=)\s*(.*)$', s, re.S)
        if dm:
            mod.assigns[dm.group(1)] = dm.group(2).strip()
            continue
        im = re.match(r'^(' + _ID + r')\s*(#\s*\()?', s)
        if not im or im.group(1) in ('assign', 'always', 'always_ff', 'always_comb', 'initial',
                                      'wire', 'reg', 'logic', 'genvar', 'integer', 'function',
                                      'task', 'typedef', 'for', 'case', 'if'):
            continue
        k = im.end()
        params = []
        if im.group(2):
            try:
                k = _balanced(s, im.end() - 1)
            except ValueError:
                continue
            for item in _split_top(s[im.end():k - 1], ','):
                pm = re.match(r'^\s*\.\s*(' + _ID + r')\s*\((.*)\)\s*$', item, re.S)
                if pm:
                    params.append((pm.group(1), pm.group(2).strip()))
                elif item.strip():
                    params.append(('', item.strip()))
        nm = re.match(r'\s*(' + _ID + r')\s*(?:\[[^\]]*\]\s*)?\(', s[k:])
        if not nm:
            continue
        start = k + nm.end() - 1
        try:
            end = _balanced(s, start)
        except ValueError:
            continue
        conns = {}
        for item in _split_top(s[start + 1:end - 1], ','):
            cm = re.match(r'^\s*\.\s*(' + _ID + r')\s*\((.*)\)\s*$', item, re.S)
            if cm:
                conns[cm.group(1)] = cm.group(2).strip()
        mod.instances.append(RtlInstance(im.group(1), nm.group(1), params, conns))
    return mod


class RtlIndex:
    """Every module defined in a set of files. A module defined in more than
    one file is AMBIGUOUS (the compile would take one; this check cannot know
    which), unless the rendered tree defines it: the render wins."""

    def __init__(self):
        self.modules: Dict[str, RtlModule] = {}
        self.where: Dict[str, List[Path]] = {}
        self.rendered: set = set()
        self.notes: List[str] = []

    def add_files(self, paths: Iterable[Path], rendered: bool = False):
        for p in paths:
            try:
                text = _strip_comments(Path(p).read_text(errors='replace'))
            except OSError as e:
                self.notes.append(f"cannot read {p}: {e}")
                continue
            starts = list(_MODULE_RE.finditer(text))
            for n, m in enumerate(starts):
                name = m.group(1)
                end = starts[n + 1].start() if n + 1 < len(starts) else len(text)
                if name in self.rendered and not rendered:
                    continue                      # the render's own module wins
                try:
                    mod = parse_module(name, text[m.start():end], Path(p))
                except (ValueError, AttributeError):
                    continue
                if rendered:
                    self.rendered.add(name)
                    self.where[name] = [Path(p)]
                else:
                    self.where.setdefault(name, [])
                    if Path(p) not in self.where[name]:
                        self.where[name].append(Path(p))
                self.modules[name] = mod

    def get(self, name: str) -> Tuple[Optional[RtlModule], str]:
        """(module, '') or (None, why)."""
        paths = self.where.get(name, [])
        if not paths:
            return None, 'source not in the render or the --rtl-flist files'
        if len(paths) > 1 and name not in self.rendered:
            return None, ('defined in ' + str(len(paths)) + ' files of the compile list ('
                          + ', '.join(str(x) for x in paths) + ')')
        return self.modules[name], ''


# ---------------------------------------------------------------------------
# File lists
# ---------------------------------------------------------------------------
_VAR_RE = re.compile(r'\$\((\w+)\)|\$\{(\w+)\}|\$(\w+)')


def _expand(text: str, missing: set) -> str:
    def sub(m):
        name = m.group(1) or m.group(2) or m.group(3)
        if name in os.environ:
            return os.environ[name]
        missing.add(name)
        return m.group(0)
    return _VAR_RE.sub(sub, text)


def read_flist(path: Path, notes: List[str], seen=None, missing_vars=None) -> List[Path]:
    """The source files a Verilog file list compiles: plain paths, -v files,
    -f/-F nested lists (relative to the list for -F), -y library dirs
    (every *.v/*.sv/*.vlib in them). +incdir/+define/+libext and comments
    are skipped. $(VAR), ${VAR} and $VAR expand from the environment; a
    name that is not set is reported, never guessed."""
    top = missing_vars is None
    missing_vars = {} if missing_vars is None else missing_vars
    seen = set() if seen is None else seen
    path = Path(path)
    if path.resolve() in seen:
        return []
    seen.add(path.resolve())
    try:
        lines = path.read_text(errors='replace').splitlines()
    except OSError as e:
        notes.append(f"--rtl-flist: cannot read {path}: {e}")
        return []
    out: List[Path] = []
    missing: set = set()
    for raw in lines:
        line = re.sub(r'//.*$', '', raw).strip()
        if not line or line.startswith('#') or line.startswith('+'):
            continue
        toks = line.split()
        k = 0
        while k < len(toks):
            t = toks[k]
            if t in ('-f', '-F', '-v', '-y') and k + 1 < len(toks):
                arg = _expand(toks[k + 1], missing)
                if t == '-F' and not os.path.isabs(arg):
                    arg = str(path.parent / arg)
                if t in ('-f', '-F'):
                    out += read_flist(Path(arg), notes, seen, missing_vars)
                elif t == '-v':
                    out.append(Path(arg))
                else:
                    d = Path(arg)
                    if d.is_dir():
                        out += sorted(p for p in d.iterdir()
                                      if p.suffix in ('.v', '.sv', '.vlib'))
                k += 2
                continue
            if t.startswith('-') or t.startswith('+'):
                k += 1
                continue
            f = Path(_expand(t, missing))
            if '$' in str(f):
                k += 1
                continue
            if f.suffix in ('.v', '.sv', '.vlib', '.svh', '.vh'):
                if f.suffix in ('.v', '.sv', '.vlib'):
                    if f.is_file():
                        out.append(f)
                    else:
                        notes.append(f"--rtl-flist {path.name}: listed file not found: {f}")
            k += 1
    for name in missing:
        missing_vars.setdefault(name, set()).add(path.name)
    if top and missing_vars:
        notes.append("--rtl-flist: not set, so the lines naming them were skipped: " + ', '.join(
            f"${n} ({len(v)} list(s))" for n, v in sorted(missing_vars.items())))
    return out


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------
@dataclass
class MemCheck:
    where: str              # rendered module path, e.g. nanosoc_multicore_soc/u_chip_core
    interconnect: str
    target: str
    instance: Optional[str]
    inst_module: Optional[str]
    table: Optional[int]    # TGT_i_SIZE written to the discovery table
    real: Optional[int]
    how: str
    verdict: str            # OK | MISMATCH | UNRESOLVED | WINDOW | WAIVED
    waiver: Optional[str] = None
    found: Optional[str] = None   # for WAIVED: the verdict without the waiver

    @property
    def key(self) -> str:
        return f"{self.interconnect}.{self.target}"

    @property
    def fails(self) -> bool:
        return self.verdict in ('MISMATCH', 'UNRESOLVED')

    def line(self) -> str:
        t = f"0x{self.table:X}" if self.table is not None else "?"
        r = f"0x{self.real:X}" if self.real is not None else "?"
        inst = f"{self.instance} ({self.inst_module})" if self.instance else "no instance"
        tail = (f" [waived: {self.waiver}; without the waiver: {self.found}]"
                if self.verdict == 'WAIVED' else '')
        return (f"{self.verdict:10s} {self.where} {self.interconnect}.{self.target}: "
                f"table {t}, compiled {r} -- {inst}: {self.how}{tail}")


FIX_UNRESOLVED = (
    "Pass the file list your build compiles (--rtl-flist <list>; $(VAR) "
    "and ${VAR} expand from the environment) so the wrapper's HADDR slice and "
    "the ROM's depth can be read, or, for a target no RTL sizes, waive it: "
    "--mem-size-waive <interconnect>.<target>=<reason>.")


def read_discovery(build_dir: Path) -> Dict[str, Dict[str, int]]:
    """{interconnect: {target: TGT_i_SIZE}} from the rendered discovery tables."""
    out: Dict[str, Dict[str, int]] = {}
    for p in sorted((Path(build_dir) / 'discovery').glob('*_discovery.yaml')):
        data = yaml.safe_load(p.read_text()) or {}
        rm = data.get('register_map', {})
        ic = re.sub(r'_discovery$', '', rm.get('name', ''))
        sizes = {}
        for reg in rm.get('registers', []):
            m = re.fullmatch(r'TGT_(\d+)_SIZE', reg.get('name', ''))
            if not m:
                continue
            dm = re.search(r'Target \d+ \((.+?)\)', reg.get('description', ''))
            if dm:
                sizes[dm.group(1)] = int(str(reg.get('reset_value')), 0)
        out[ic] = sizes
    return out


def _haddr_slice(expr: str) -> Optional[Tuple[str, str]]:
    m = re.fullmatch(r'\s*HADDR\s*\[(.+?):(.+?)\]\s*', expr or '')
    return (m.group(1), m.group(2)) if m else None


class MemSizeGuard:
    def __init__(self, top, build_dir: Path, rtl_flists: Iterable[Path] = (),
                 config_overrides: Optional[Dict] = None, waivers: Optional[Dict[str, str]] = None):
        self.top = top
        self.build_dir = Path(build_dir)
        self.notes: List[str] = []
        self.errors: List[str] = []
        self.waivers: Dict[str, str] = dict(waivers or {})
        self.index = RtlIndex()
        rendered = [p for p in (self.build_dir / 'rtl').rglob('*')
                    if p.is_file() and p.suffix in ('.sv', '.v')]
        self.index.add_files(rendered, rendered=True)
        self.flists = [Path(f) for f in rtl_flists]
        files: List[Path] = []
        for f in self.flists:
            files += read_flist(f, self.notes)
        self.index.add_files(files)
        self.notes += self.index.notes
        self.overrides = {k: v for k, v in (config_overrides or {}).items()
                          if k in top.params and isinstance(v, int)}

    def _verdict(self, c: MemCheck) -> MemCheck:
        """Apply a waiver, if one names this target. A waived target keeps
        what the guard found, so the waiver never hides it."""
        if c.verdict == 'UNRESOLVED':
            c.how += ' -- ' + FIX_UNRESOLVED
        reason = self.waivers.get(c.key)
        if reason is not None:
            c.found, c.verdict, c.waiver = c.verdict, 'WAIVED', reason
            if c.found in ('OK', 'WINDOW'):
                # a waiver on a target that passes is stale: it would hide the
                # next real defect on that target. Refused, not ignored.
                why = ('the sizes agree' if c.found == 'OK' else
                       'it is a subsystem window, checked at its own interconnect')
                c.how += f' ({why}: this waiver is not needed)'
                self.errors.append(
                    f"--mem-size-waive {c.key}: a stale waiver: {why} (found {c.found}). "
                    f"Remove the waiver.")
        return c

    # -- sizing ---------------------------------------------------------------
    def _size_in(self, mod: RtlModule, env, depth: int, trail: List[str]):
        """(bytes, how) of the one memory inside `mod`, (None, why) if it
        cannot be sized or there are several, ('', '') if there is none."""
        # nets this module derives from HADDR (assign a = HADDR[...]): an
        # instance taking one of them, or HADDR itself, is on the address path
        haddr_nets = {n for n, rhs in mod.assigns.items() if re.search(r'\bHADDR\b', rhs)}

        def on_address_path(expr):
            return any(re.search(r'\b' + re.escape(n) + r'\b', expr or '')
                       for n in haddr_nets | {'HADDR'})

        found = []
        for inst in mod.instances:
            here = trail + [inst.name]
            if inst.module in RAM_PRIMITIVES:
                pname = RAM_PRIMITIVES[inst.module]
                expr, scope = dict(inst.params).get(pname), env
                if expr is None:                      # not overridden: the cell's default
                    sub, _ = self.index.get(inst.module)
                    if sub is not None:
                        expr, scope = dict(sub.params).get(pname), sub.env({})
                w = try_const(expr, scope)
                if w is None:
                    found.append((None, f"{'/'.join(here)}: cannot evaluate "
                                        f"{inst.module}.{pname} = {expr!r}"))
                else:
                    found.append((1 << w, f"{'/'.join(here)} is {inst.module} with "
                                          f"{pname}={w} (a BYTE width): 2**{w} bytes"))
                continue
            if 'word_addr' in inst.conns:
                wa = inst.conns['word_addr'].strip()
                wa = mod.assigns.get(wa, wa)          # through one `assign net = HADDR[..]`
                found.append(self._rom(inst, _haddr_slice(wa), wa, env, trail))
                continue
            sub, _ = self.index.get(inst.module)
            if sub is not None and depth < 4 and any(on_address_path(e) for e in inst.conns.values()):
                over = {}
                for pn, pe in inst.params:
                    v = try_const(pe, env)
                    if pn and v is not None:
                        over[pn] = v
                r = self._size_in(sub, sub.env(over), depth + 1, here)
                if r != ('', ''):
                    found.append(r)
        if not found:
            return '', ''
        if len(found) > 1:
            return None, (f"{len(found)} memories under one target: "
                          + '; '.join(str(h) for _, h in found))
        return found[0]

    def _rom(self, inst: RtlInstance, sl, wa: str, env, trail):
        here = '/'.join(trail + [inst.name])
        if sl is None:
            return None, f"{here}: word_addr is driven by {wa!r}, not a HADDR slice"
        hi, lo = try_const(sl[0], env), try_const(sl[1], env)
        if hi is None or lo is None:
            return None, f"{here}: cannot evaluate the slice HADDR[{sl[0]}:{sl[1]}]"
        if lo != 2:
            return None, f"{here}: word_addr takes HADDR[{hi}:{lo}], not a word address (lo must be 2)"
        dec_w = hi - lo + 1
        rom, why = self.index.get(inst.module)
        if rom is None:
            return None, (f"{here}: the decode reaches 2**{dec_w} words, but the ROM "
                          f"{inst.module} is not readable ({why}); its depth is unknown")
        rng = rom.ports.get('word_addr')
        if rng is None:
            return None, f"{here}: {inst.module} has no word_addr port"
        rom_env = rom.env({pn: v for pn, v in ((p, try_const(e, env)) for p, e in inst.params)
                           if pn and v is not None})
        rm = re.fullmatch(r'\s*(.+?)\s*:\s*(.+?)\s*', rng or '')
        rhi = try_const(rm.group(1), rom_env) if rm else None
        rlo = try_const(rm.group(2), rom_env) if rm else None
        if rhi is None or rlo is None:
            return None, f"{here}: cannot evaluate {inst.module}'s word_addr range [{rng}]"
        rom_w = rhi - rlo + 1
        eff = min(dec_w, rom_w)
        note = ''
        if rom_w > dec_w:
            note = f"; only 2**{dec_w} of the ROM's 2**{rom_w} words are reachable"
        elif rom_w < dec_w:
            note = f"; the ROM repeats {1 << (dec_w - rom_w)}x in the decoded window"
        return (WORD_BYTES << eff,
                f"{here} slices HADDR[{sl[0]}:{sl[1]}] = [{hi}:{lo}] ({dec_w}-bit word address) into "
                f"{inst.module} word_addr[{rng}] ({rom_w} bits): 4 * 2**{eff} bytes{note}")

    def _size(self, inst_rtl: Optional[RtlInstance], inst_module: str, env_over: Dict[str, int]):
        """(bytes, how, kind): kind is 'sized', 'window' or 'unresolved'."""
        mod, why = self.index.get(inst_module)
        if mod is not None and inst_module in self.index.rendered and inst_module in self.subsystems:
            return None, (f"a window onto the generated subsystem {inst_module}, not one memory; "
                          f"its memories are checked at its own interconnect"), 'window'
        if mod is not None:
            r = self._size_in(mod, mod.env(env_over), 0, [inst_rtl.name if inst_rtl else inst_module])
            if r == ('', ''):
                return None, (f"no RAM or ROM in {inst_module} ({mod.path}): nothing compiled "
                              f"sizes this window (an off-chip device?)"), 'unresolved'
            return r[0], r[1], ('sized' if r[0] is not None else 'unresolved')
        if 'RAM_ADDR_W' in env_over:
            w = env_over['RAM_ADDR_W']
            return (1 << w, f"RAM region (its source is not in the compile list): RAM_ADDR_W={w}, "
                            f"the sl_ahb_sram BYTE width: 2**{w} bytes", 'sized')
        if 'ROM_ADDR_W' in env_over:
            return None, (f"boot ROM: its unit is its wrapper's (HADDR slice and ROM depth), "
                          f"and {inst_module}'s {why}"), 'unresolved'
        return None, f"no size rule for {inst_module} ({why})", 'unresolved'

    # -- walk -----------------------------------------------------------------
    def run(self) -> List[MemCheck]:
        tables = read_discovery(self.build_dir)
        self.subsystems = set()

        def collect(m):
            for i in m.instances:
                sub = i.resolved_module
                if sub is not None and sub.gen and (sub.interconnects or sub.instances):
                    if sub.name not in self.subsystems:
                        self.subsystems.add(sub.name)
                        collect(sub)
        collect(self.top)
        checks: List[MemCheck] = []
        top_rtl, why = self.index.get(self.top.name)
        if top_rtl is None:
            self.errors.append(f"the rendered top {self.top.name} was not found ({why}); "
                               f"no memory size was checked")
            return checks
        self._walk(self.top, top_rtl, top_rtl.env(self.overrides), self.top.name, tables, checks)
        # a waiver must name a memory target that exists: a stale or misspelt
        # waiver is refused, not ignored
        known = {c.key for c in checks}
        for k in sorted(self.waivers):
            if k not in known:
                self.errors.append(
                    f"--mem-size-waive {k}: no memory target of that name (known: "
                    f"{', '.join(sorted(known)) or 'none'})")
        return checks

    def _walk(self, model, rtl: RtlModule, env, where, tables, checks):
        by_name = {i.name: i for i in rtl.instances}
        for ic in model.interconnects:
            if not ic.gen:
                continue
            sizes = tables.get(ic.name, {})
            for t in ic.targets:
                if (t.region_type or '').lower() != 'memory':
                    continue
                # The instance behind the target is the one CONNECTED to it (the
                # bus the render wires). `instance:` is a claim about it: when
                # both are given they must agree, and a WINDOW needs the
                # connected instance to be that subsystem -- `instance:` alone
                # never makes a target a window.
                table = sizes.get(t.name)
                wired = [x for x in model.instances if any(
                    c.conn == f"{ic.name}.{t.name}" for c in x.connections)]
                conn_inst = wired[0] if len(wired) == 1 else None
                inst = conn_inst
                if t.instance:
                    named = next((x for x in model.instances if x.instance_name == t.instance), None)
                    if named is None or (wired and not any(x is named for x in wired)) or len(wired) > 1:
                        on = ', '.join(x.instance_name for x in wired) or 'no instance'
                        checks.append(self._verdict(MemCheck(
                            where, ic.name, t.name, t.instance,
                            named.module_name if named else None, table, None,
                            f"the target's `instance: {t.instance}` "
                            f"{'names no instance of ' + model.name if named is None else 'disagrees with the connection'}"
                            f": {on} {'is' if len(wired) < 2 else 'are'} connected to "
                            f"{ic.name}.{t.name}. Make `instance:` name the connected instance",
                            'UNRESOLVED')))
                        continue
                    inst = named
                elif len(wired) > 1:
                    checks.append(self._verdict(MemCheck(
                        where, ic.name, t.name, None, None, table, None,
                        f"{len(wired)} instances are connected to this target "
                        f"({', '.join(x.instance_name for x in wired)})", 'UNRESOLVED')))
                    continue
                if inst is None:
                    checks.append(self._verdict(MemCheck(
                        where, ic.name, t.name, None, None, table, None,
                        "no instance is connected to this target", 'UNRESOLVED')))
                    continue
                irtl = by_name.get(inst.instance_name)
                if irtl is None:
                    checks.append(self._verdict(MemCheck(
                        where, ic.name, t.name, inst.instance_name, inst.module_name, table, None,
                        f"{inst.instance_name} is not in the rendered {rtl.name}", 'UNRESOLVED')))
                    continue
                over = {}
                for pn, pe in irtl.params:
                    v = try_const(pe, env)
                    if pn and v is not None:
                        over[pn] = v
                real, how, kind = self._size(irtl, irtl.module, over)
                if kind == 'window' and conn_inst is not inst:
                    verdict, how = 'UNRESOLVED', (
                        f"{how}; but no instance is connected to {ic.name}.{t.name}: "
                        f"`instance: {t.instance}` alone does not make a target a window")
                elif kind == 'window':
                    verdict = 'WINDOW'
                elif table is None:
                    verdict, how = 'UNRESOLVED', f"no TGT size for it in {ic.name}_discovery.yaml; {how}"
                elif real is None:
                    verdict = 'UNRESOLVED'
                else:
                    verdict = 'OK' if real == table else 'MISMATCH'
                    if verdict == 'MISMATCH':
                        r = table / real
                        how += (f" -- the table says {r:g}x the memory" if r >= 1
                                else f" -- the table says 1/{real / table:g} of the memory")
                checks.append(self._verdict(MemCheck(where, ic.name, t.name, inst.instance_name,
                                                     irtl.module, table, real, how, verdict)))
        for inst in model.instances:
            sub = inst.resolved_module
            if sub is None or not (sub.gen and (sub.interconnects or sub.instances)):
                continue
            irtl = by_name.get(inst.instance_name)
            srtl, _ = self.index.get(sub.name)
            if irtl is None or srtl is None:
                continue
            over = {}
            for pn, pe in irtl.params:
                v = try_const(pe, env)
                if pn and v is not None:
                    over[pn] = v
            self._walk(sub, srtl, srtl.env(over), f"{where}/{inst.instance_name}", tables, checks)


def parse_waivers(items: Iterable[str]) -> Dict[str, str]:
    """`<interconnect>.<target>=<reason>` strings -> {key: reason}. A waiver
    without a reason, or one given twice, is a ValueError."""
    out: Dict[str, str] = {}
    for it in items or ():
        key, eq, reason = str(it).partition('=')
        key, reason = key.strip(), reason.strip()
        if not eq or not re.fullmatch(_ID + r'\.' + _ID, key) or not reason:
            raise ValueError(f"--mem-size-waive {it!r}: expected <interconnect>.<target>=<reason>, "
                             f"with a reason")
        if key in out:
            raise ValueError(f"--mem-size-waive {key}: given twice")
        out[key] = reason
    return out


def guard_render(top, build_dir: Path, rtl_flists=(), config_overrides=None, waivers=None):
    """(checks, notes, errors) for a finished render. `errors` are failures
    that are not one target's verdict (a waiver naming no target, no top)."""
    g = MemSizeGuard(top, build_dir, rtl_flists, config_overrides, waivers)
    return g.run(), g.notes, g.errors
