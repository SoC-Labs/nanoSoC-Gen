# Copyright 2026, SoC Labs (www.soclabs.org)
"""HSEL-reader lint: every matrix-driven ``<tgt>_hsel`` must have a reader.

A bus-matrix target port legally shows HTRANS=NONSEQ with HSEL=0 for a cycle
when its last master moves on to another slave. Whatever sits behind the port
must therefore qualify HTRANS by HSEL. A leaf slave does that by reading its
HSEL input. A child subsystem's passthrough initiator does NOT (its own matrix
ties HSEL high), so the generator has to gate the link. Every time that gate
was missing, the symptom was the same in the rendered RTL: the matrix drove
``<tgt>_hsel`` and nothing outside the matrix ever read it (cpu_ss_hsel
declared and unused; eth_ss_slave_hsel wired to the matrix only).

This check reads the RENDERED RTL, not the model, so it judges what was
actually emitted, and it runs on any past render (``python -m
soc_model.backends.hsel_lint <rtl_dir>``).

Definitions
-----------
Structural module
    A module rendered by nanosoc_gen from ``soc_toplevel.sv.j2`` (a SoC top
    or a subsystem wrapper) or ``soc_system.sv.j2``. Only these are analysed
    net by net.
Matrix
    Within a structural module, the instance named ``u_interconnect``, or any
    instance of a module rendered from an ``ahb_interconnect*`` template. Its
    internals are never inspected.
Matrix-driven hsel
    A net connected to a matrix port whose name ends in ``_hsel`` (and which
    is an output when the matrix wrapper source is available). A matrix hsel
    port left open while its ``_htrans`` is connected is also reported.
Reader of net N in structural module M (anything else is not a reader)
    1. N appears in the right-hand side of a continuous assign in M, inside
       an expression (``assign x = N ? t : 2'b00``). A plain copy
       (``assign x = N``, also into a bit-select or a concatenation) is
       followed: the copy must itself have a reader.
    2. N is connected to an input port P of an instance of a module T that
       is not the matrix (by name, by ``.P`` or through ``.*``):
         * T structural: P must have a reader inside T (recursively);
         * T's source is in the render but T is not structural: P must be
           used in T's body other than in its own declaration (in any
           statement, including an ``if``/``case``/``for`` condition);
         * T's source is not in the render (a leaf peripheral / vendor IP):
           the HSEL input counts as a reader.
    3. N is an output port of M: followed into every structural parent that
       instantiates M; if M is a root (nothing in the render instantiates
       it), the export counts as a reader (the consumer is outside the
       render).
    4. Any other use of N in M (procedural code, an ``if``/``case``
       condition, a function argument, ...) counts as a reader.
    A port that is declared and never used is not a reader, and neither is
    the matrix itself.

Rule 2 -- module INPUT hsel (the 036d381 form). Every input port named
``hsel`` / ``<x>_hsel`` of a structural module must have a reader (as above)
in that module whenever the matching ``htrans`` / ``<x>_htrans`` input is
used there at all (its own matrix included): the module takes the transfer
but not its select. When neither port of the pair is used, the interface is
simply unconnected; that is reported as a NOTE, not a failure.

Unanalysable (reported as a NOTE, never a failure): a copy of N into a
struct field or interface signal (``s.hsel = N``), and a ``.*`` connection
to a module whose source is not in the render. The templates emit neither.

Limits. The rule is built to err towards "read": an hsel used only in logic
whose result is itself unused, or connected to a leaf outside the render
that ignores it, passes. Comments and strings are removed before parsing
(a quote inside a comment, or ``/*`` inside a ``//`` comment, is handled);
both branches of an ```ifdef`` are kept and macros are not expanded. It
parses nanosoc_gen's structural output and ordinary hand-written Verilog; a
construct it mis-parses is a bug in this file, not in the design.
"""

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

STRUCTURAL_TEMPLATES = ('soc_toplevel.sv.j2', 'soc_system.sv.j2')
MATRIX_TEMPLATE_PREFIX = 'ahb_interconnect'
MATRIX_INSTANCE = 'u_interconnect'

_ID = r'[A-Za-z_][A-Za-z0-9_$]*'
_ID_RE = re.compile(_ID)
_NUM_RE = re.compile(r"\d*\s*'\s*[sS]?[bBoOdDhH]\s*[0-9a-fA-FxXzZ_?]+|'[01xXzZ]")
_KEYWORDS = {
    'module', 'endmodule', 'input', 'output', 'inout', 'wire', 'reg', 'logic',
    'tri', 'assign', 'parameter', 'localparam', 'generate', 'endgenerate',
    'if', 'else', 'begin', 'end', 'for', 'genvar', 'signed', 'unsigned',
    'always', 'always_comb', 'always_ff', 'initial', 'posedge', 'negedge',
    'or', 'and', 'not', 'case', 'endcase', 'default', 'integer', 'function',
    'endfunction', 'int', 'bit',
}
_DECL_RE = re.compile(r'^(input|output|inout|wire|reg|logic|tri|localparam|parameter|genvar|integer)\b')


@dataclass
class SvInstance:
    module: str
    name: str
    conns: List[Tuple[str, Optional[str]]]   # (port, expr or None if open)


@dataclass
class SvModule:
    name: str
    path: Path
    structural: bool = False
    matrix: bool = False
    ports: Dict[str, str] = field(default_factory=dict)       # name -> dir
    assigns: List[Tuple[str, str]] = field(default_factory=list)
    instances: List[SvInstance] = field(default_factory=list)
    other: List[str] = field(default_factory=list)            # unclassified stmts
    body: str = ''


UNANALYSABLE = 'unanalysable: '


@dataclass
class Finding:
    module: str
    net: str
    matrix_port: str
    trace: List[str]
    # Is the port's HTRANS consumed by anything outside the matrix? True: the
    # target sees transfers without their HSEL. False: the port is dangling.
    htrans_used: Optional[bool] = None
    kind: str = 'matrix'          # 'matrix' (rule 1) or 'input' (rule 2)

    def __str__(self):
        if self.kind == 'input':
            head = f"{self.module}: input {self.net}"
        else:
            head = f"{self.module}: {self.net} (matrix port {self.matrix_port})"
        if self.trace and all(t.startswith(UNANALYSABLE) for t in self.trace):
            return (f"{head} cannot be analysed [" +
                    '; '.join(t[len(UNANALYSABLE):] for t in self.trace) + "]")
        if self.kind == 'input':
            why = 'is never read [' + '; '.join(self.trace) + ']'
        elif self.trace:
            why = 'has no reader outside the matrix [' + '; '.join(self.trace) + ']'
        else:
            why = 'has no reader outside the matrix [connected to nothing else]'
        if self.kind == 'input' and self.htrans_used is True:
            then = (f'; the module uses {self.matrix_port} without it, so it can '
                    'take a transfer its parent matrix did not select')
        elif self.kind == 'input' and self.htrans_used is False:
            then = f'; {self.matrix_port} is unused too: the interface is unconnected'
        elif self.htrans_used is True:
            then = ('; its htrans IS used, so the target behind it can take a '
                    'transfer the matrix did not select')
        elif self.htrans_used is False:
            then = ('; its htrans is unused too: the target port is dangling')
        else:
            then = ''
        return f"{head} {why}{then}"


# ---------------------------------------------------------------------------
# Lexing helpers
# ---------------------------------------------------------------------------
_LEX_RE = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"', re.S)


def _lex_sub(m) -> str:
    tok = m.group(0)
    if tok.startswith('"'):
        return '""'
    return ' ' + '\n' * tok.count('\n')   # a comment: keep the line count


def _strip_comments(text: str) -> str:
    """Remove comments and string contents in ONE left-to-right pass, so
    whichever starts first wins: a `"` inside a comment, or `/*` inside a
    `//` comment, cannot swallow the code that follows."""
    text = _LEX_RE.sub(_lex_sub, text)
    # preprocessor directives (`timescale, `include, `ifdef ...): drop the line
    text = re.sub(r'^\s*`[^\n]*', '', text, flags=re.M)
    return text


def _idents(expr: str) -> List[str]:
    expr = _NUM_RE.sub(' ', expr)
    return [t for t in _ID_RE.findall(expr) if t not in _KEYWORDS]


def _balanced(text: str, i: int) -> int:
    """Index just past the ')' matching the '(' at text[i]."""
    depth = 0
    for j in range(i, len(text)):
        c = text[j]
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return j + 1
    raise ValueError('unbalanced parentheses')


def _split_top(text: str, sep: str) -> List[str]:
    """Split on `sep` at parenthesis/brace depth 0."""
    out, depth, cur = [], 0, []
    for c in text:
        if c in '({[':
            depth += 1
        elif c in ')}]':
            depth -= 1
        if c == sep and depth == 0:
            out.append(''.join(cur))
            cur = []
        else:
            cur.append(c)
    out.append(''.join(cur))
    return out


def _bare(expr: Optional[str]) -> Optional[str]:
    """The net name if `expr` is a plain net (optionally bit-selected)."""
    if expr is None:
        return None
    m = re.fullmatch(r'\s*(' + _ID + r')\s*(\[[^\]]*\])?\s*', expr)
    return m.group(1) if m else None


_GEN_PREFIX = re.compile(
    r'^\s*(?:(?:generate|endgenerate|begin|end|else|endcase|fork|join|join_any|join_none)'
    r'\b\s*(?::\s*' + _ID + r')?\s*)+')
_COND_PREFIX = re.compile(r'^\s*(?:if|for|while|repeat|case|casez|casex|@)\s*\(')
_BLOCK_PREFIX = re.compile(
    r'^\s*(?:always_comb|always_latch|always_ff|always|initial|final)\b\s*'
    r'(?:@\s*\*|@\s*' + _ID + r')?')


def _peel(stmt: str) -> Tuple[str, List[str]]:
    """Split a statement into (rest, conditions).

    Removes generate/begin/end/else scaffolding, ``always``/``initial``
    headers, and leading ``if``/``for``/``while``/``case``/``@`` clauses.
    The text inside every removed clause is returned as a condition: it is
    READ by the statement (``if (HSEL & HTRANS[1]) ...`` reads HSEL), so a
    caller must count its identifiers as uses, never drop them.
    """
    conds: List[str] = []
    prev = None
    while prev != stmt:
        prev = stmt
        stmt = _GEN_PREFIX.sub('', stmt)
        stmt = _BLOCK_PREFIX.sub('', stmt)
        m = _COND_PREFIX.match(stmt)
        if m:
            end = _balanced(stmt, m.end() - 1)
            conds.append(stmt[m.end():end - 1])
            stmt = stmt[end:]
    return stmt.strip(), conds


def _is_declaration(stmt: str) -> bool:
    """A port / net declaration with no initialiser (reads nothing)."""
    if re.match(r'^(input|output|inout)\b', stmt):
        return True
    return bool(re.match(r'^(wire|reg|logic|tri|genvar|integer|typedef)\b', stmt)) \
        and '=' not in stmt


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
_MODULE_RE = re.compile(r'\bmodule\s+(' + _ID + r')')


def _parse_header_ports(ports_text: str) -> Dict[str, str]:
    ports, direction = {}, None
    for item in _split_top(ports_text, ','):
        toks = _idents(re.sub(r'\[[^\]]*\]', ' ', item))
        m = re.match(r'\s*(input|output|inout)\b', item)
        if m:
            direction = m.group(1)
        names = [t for t in toks if t not in ('input', 'output', 'inout')]
        if names and direction:
            ports[names[-1]] = direction
    return ports


def parse_module_text(name: str, text: str, path: Path, header: str) -> SvModule:
    """Parse one comment-stripped `module ... endmodule` block."""
    mod = SvModule(name=name, path=path)
    mod.structural = any(f'Template:  {t}' in header or f'Template: {t}' in header
                         for t in STRUCTURAL_TEMPLATES)
    mod.matrix = bool(re.search(r'Template:\s+' + MATRIX_TEMPLATE_PREFIX, header))
    m = _MODULE_RE.search(text)
    i = m.end()
    # optional #( params )
    rest = text[i:]
    j = 0
    mm = re.match(r'\s*#\s*\(', rest)
    if mm:
        j = _balanced(rest, mm.end() - 1)
    mp = re.match(r'\s*\(', rest[j:])
    if mp:
        k = j + mp.end() - 1
        end = _balanced(rest, k)
        mod.ports = _parse_header_ports(rest[k + 1:end - 1])
        body = rest[end:]
    else:
        body = rest[j:]
    body = body.split(';', 1)[1] if ';' in body else body
    body = re.sub(r'\bendmodule\b.*$', '', body, flags=re.S)
    mod.body = body
    for stmt in _split_top(body, ';'):
        stmt, conds = _peel(stmt)
        mod.other += conds          # a condition reads what it names
        if not stmt:
            continue
        if _DECL_RE.match(stmt):
            dm = re.match(r'^(input|output|inout)\b', stmt)
            if dm:   # non-ANSI port declarations
                for n in _idents(re.sub(r'\[[^\]]*\]', ' ', stmt.split('=')[0])):
                    mod.ports.setdefault(n, dm.group(1))
            if '=' in stmt and not stmt.startswith(('parameter', 'localparam')):
                lhs, rhs = stmt.split('=', 1)
                decl = _idents(re.sub(r'\[[^\]]*\]', ' ', lhs))
                if decl:
                    mod.assigns.append((decl[-1], rhs))
            continue
        am = re.match(r'^assign\s+(.*?)\s*=(?!=)\s*(.*)$', stmt, flags=re.S)
        if am:
            lhs = _bare(am.group(1)) or am.group(1)
            mod.assigns.append((lhs, am.group(2)))
            continue
        im = re.match(r'^(' + _ID + r')\s*(#\s*\()?', stmt)
        if im:
            k = im.end()
            if im.group(2):
                k = _balanced(stmt, im.end() - 1)
            nm = re.match(r'\s*(' + _ID + r')\s*\(', stmt[k:])
            if nm:
                start = k + nm.end() - 1
                try:
                    end = _balanced(stmt, start)
                except ValueError:
                    end = None
                if end is not None and not stmt[end:].strip():
                    conns = []
                    for item in _split_top(stmt[start + 1:end - 1], ','):
                        cm = re.match(r'\s*\.\s*(' + _ID + r')\s*\((.*)\)\s*$', item, flags=re.S)
                        im2 = re.fullmatch(r'\s*\.\s*(' + _ID + r')\s*', item)
                        if cm:
                            expr = cm.group(2).strip()
                            conns.append((cm.group(1), expr if expr else None))
                        elif im2:                              # .name
                            conns.append((im2.group(1), im2.group(1)))
                        elif re.fullmatch(r'\s*\.\s*\*\s*', item):   # .*
                            conns.append(('*', '*'))
                        elif item.strip():
                            conns.append(('', item.strip()))   # positional
                    mod.instances.append(SvInstance(im.group(1), nm.group(1), conns))
                    continue
        mod.other.append(stmt)
    return mod


def index_rtl(paths: Iterable[Path]) -> Dict[str, SvModule]:
    """Parse every module declared in the given .sv/.v files.

    When a module is declared twice the shallowest path wins (a generated
    wrapper sits at the top of rtl/, stale or vendor copies deeper down).
    """
    mods: Dict[str, SvModule] = {}
    depth: Dict[str, int] = {}
    for p in sorted(paths, key=lambda q: (len(q.parts), str(q))):
        try:
            raw = p.read_text(errors='replace')
        except OSError:
            continue
        header = raw[:2000]
        text = _strip_comments(raw)
        starts = [m for m in _MODULE_RE.finditer(text)]
        for n, m in enumerate(starts):
            end = starts[n + 1].start() if n + 1 < len(starts) else len(text)
            name = m.group(1)
            if name in mods and depth[name] <= len(p.parts):
                continue
            chunk = text[m.start():end]
            try:
                mod = parse_module_text(name, chunk, p, header)
            except (ValueError, AttributeError):
                mod = SvModule(name=name, path=p, body=chunk)
            mods[name] = mod
            depth[name] = len(p.parts)
    return mods


def rtl_files(rtl_dir: Path) -> List[Path]:
    return [p for p in Path(rtl_dir).rglob('*')
            if p.is_file() and p.suffix in ('.sv', '.v')]


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------
class HselReaderLint:
    def __init__(self, mods: Dict[str, SvModule]):
        self.mods = mods
        self.checked = 0
        self.checked_inputs = 0
        self.unanalysable: List[Finding] = []
        self.unused_inputs: List[Finding] = []
        self.modules: List[SvModule] = []
        self.parents: Dict[str, List[Tuple[SvModule, SvInstance]]] = {}
        for m in mods.values():
            if not m.structural:
                continue
            for inst in m.instances:
                self.parents.setdefault(inst.module, []).append((m, inst))

    def _is_matrix(self, inst: SvInstance) -> bool:
        if inst.name == MATRIX_INSTANCE:
            return True
        t = self.mods.get(inst.module)
        return bool(t and t.matrix)

    def _port_used_textually(self, mod: SvModule, port: str, depth: int = 0) -> str:
        """'yes' / 'no' / 'unanalysable': is input `port` used in the body
        of the non-structural module `mod`?

        The port is matched against the UNPEELED statement: a condition
        (``if (HSEL & HTRANS[1])``, ``else if (HSEL)``, ``case (HSEL)``) is
        a use. Only a declaration without an initialiser is not.
        """
        for stmt in _split_top(mod.body, ';'):
            try:
                rest, conds = _peel(stmt)
            except ValueError:
                rest, conds = stmt.strip(), []
            if any(port in _idents(c) for c in conds):
                return 'yes'
            if not rest or _is_declaration(rest):
                continue
            if port in _idents(rest):
                return 'yes'
        # `.*` passes every same-named port through to the child instance.
        for inst in mod.instances:
            if ('*', '*') not in inst.conns or any(p == port for p, _ in inst.conns):
                continue
            t = self.mods.get(inst.module)
            if t is None or depth > 8:
                return 'unanalysable'
            if port in t.ports and t.ports[port] != 'output':
                if t.structural:
                    r, _ = self.readers(t, port)
                    return 'yes' if r else 'no'
                return self._port_used_textually(t, port, depth + 1)
        return 'no'

    def _conns_for(self, inst: SvInstance, net: str) -> List[Tuple[str, Optional[str]]]:
        """Connections of `inst` that may carry `net`, with a ``.*`` resolved
        against the instantiated module's ports when its source is present
        (('*', '*') is kept when it is not: unanalysable)."""
        out = [(p, e) for p, e in inst.conns if p != '*']
        if ('*', '*') in inst.conns and not any(p == net for p, _ in out):
            t = self.mods.get(inst.module)
            if t is None:
                out.append(('*', '*'))
            elif net in t.ports:
                out.append((net, net))
        return out

    def readers(self, mod: SvModule, net: str,
                seen: Optional[Set[Tuple[str, str]]] = None) -> Tuple[List[str], List[str]]:
        """(readers, dead-ends) of `net` in structural `mod`."""
        seen = set() if seen is None else seen
        key = (mod.name, net)
        if key in seen:
            return [], []
        seen.add(key)
        found: List[str] = []
        dead: List[str] = []
        for lhs, rhs in mod.assigns:
            if net not in _idents(rhs):
                continue
            if _bare(rhs) != net:
                found.append(f'{mod.name}: assign {lhs} = {" ".join(rhs.split())}')
                continue
            # A plain copy: follow it.
            if '.' in lhs:
                # struct field / interface signal: not resolved here.
                found.append(f'{UNANALYSABLE}{mod.name}: copied to {lhs.strip()} '
                             f'(struct or interface member)')
                continue
            targets = [_bare(lhs)] if _bare(lhs) else \
                _idents(re.sub(r'\[[^\]]*\]', ' ', lhs))      # {a, b} = ...
            for target in targets:
                if mod.ports.get(target) == 'output':
                    r, d = self._through_port(mod, target, seen)
                    found += r
                    dead += d
                else:
                    r, d = self.readers(mod, target, seen)
                    found += r
                    dead += d or ([] if r else [f'{mod.name}: copied to {target}, which nothing reads'])
        for inst in mod.instances:
            if self._is_matrix(inst):
                continue
            for port, expr in self._conns_for(inst, net):
                if port == '*':
                    found.append(f'{UNANALYSABLE}{mod.name}.{inst.name} connects .* to '
                                 f'{inst.module}, whose source is not in the render')
                    continue
                if expr is None or net not in _idents(expr):
                    continue
                where = f'{mod.name}.{inst.name}.{port or "?"}'
                if _bare(expr) != net or not port:
                    found.append(f'{where} (expression {expr})')
                    continue
                t = self.mods.get(inst.module)
                if t is None:
                    found.append(f'{where} (leaf {inst.module}, source not in render)')
                    continue
                pdir = t.ports.get(port)
                if pdir == 'output':
                    continue
                if pdir is None and t.ports:
                    dead.append(f'{t.name} has no port {port}')
                    continue
                if t.structural:
                    r, d = self.readers(t, port, seen)
                    found += r
                    dead += d or ([] if r else [f'{t.name}.{port} declared and not used'])
                    continue
                used = self._port_used_textually(t, port)
                if used == 'yes':
                    found.append(f'{where} (read in {t.name})')
                elif used == 'unanalysable':
                    found.append(f'{UNANALYSABLE}{t.name}.{port} is passed on through .* '
                                 f'to a module whose source is not in the render')
                else:
                    dead.append(f'{t.name}.{port} declared and not used')
        for stmt in mod.other:
            if net in _idents(stmt):
                found.append(f'{mod.name}: {" ".join(stmt.split())[:80]}')
        if mod.ports.get(net) == 'output':
            r, d = self._through_port(mod, net, seen)
            found += r
            dead += d
        return found, dead

    def _through_port(self, mod: SvModule, port: str, seen) -> Tuple[List[str], List[str]]:
        parents = self.parents.get(mod.name, [])
        if not parents:
            return [f'{mod.name}: exported as output {port} (consumer outside the render)'], []
        found, dead = [], []
        for pmod, inst in parents:
            expr = dict(inst.conns).get(port)
            n = _bare(expr)
            if n is None:
                dead.append(f'{pmod.name}.{inst.name}.{port} left open')
                continue
            r, d = self.readers(pmod, n, seen)
            found += r
            dead += d
        return found, dead

    def _net_consumed(self, mod: SvModule, net: Optional[str]) -> bool:
        """Does anything in `mod` other than the matrix take `net` (one level:
        a child's own matrix input counts as a consumer here)?"""
        if not net:
            return False
        if mod.ports.get(net) == 'output':
            return True
        if any(net in _idents(rhs) for _, rhs in mod.assigns):
            return True
        if any(net in _idents(st) for st in mod.other):
            return True
        return any(e is not None and net in _idents(e)
                   for inst in mod.instances if not self._is_matrix(inst)
                   for _, e in inst.conns)

    def check_module(self, mod: SvModule, log: Optional[list] = None) -> List[Finding]:
        """Findings for `mod`; with `log`, also append (net, first reader)
        for every matrix-driven hsel that passed. Every hsel examined is
        counted in self.checked; unanalysable ones go to self.unanalysable."""
        findings = []
        for inst in mod.instances:
            if not self._is_matrix(inst):
                continue
            wrapper = self.mods.get(inst.module)
            conns = dict(inst.conns)
            for port, expr in inst.conns:
                if not port.endswith('_hsel'):
                    continue
                if wrapper is not None and wrapper.ports and \
                        wrapper.ports.get(port) not in (None, 'output'):
                    continue
                htrans_net = _bare(conns.get(port[:-5] + '_htrans'))
                net = _bare(expr)
                if net is None:
                    if expr is None and htrans_net is not None:
                        self.checked += 1
                        findings.append(Finding(
                            mod.name, '(open)', f'{inst.name}.{port}',
                            ['hsel left open while htrans is connected'],
                            self._net_consumed(mod, htrans_net)))
                    continue
                self.checked += 1
                found, dead = self.readers(mod, net)
                real = [f for f in found if not f.startswith(UNANALYSABLE)]
                if not found:
                    findings.append(Finding(mod.name, net, f'{inst.name}.{port}', dead,
                                            self._net_consumed(mod, htrans_net)))
                elif not real:
                    self.unanalysable.append(Finding(mod.name, net, f'{inst.name}.{port}', found))
                elif log is not None:
                    log.append((f'{mod.name}.{net}', real[0]))
        findings += self._check_input_hsels(mod, log)
        return findings

    def _net_used(self, mod: SvModule, net: str) -> bool:
        """Is `net` used anywhere in `mod`, the matrix included?"""
        if any(net in _idents(rhs) for _, rhs in mod.assigns):
            return True
        if any(net in _idents(st) for st in mod.other):
            return True
        return any(e is not None and net in _idents(e)
                   for inst in mod.instances for _, e in inst.conns)

    def _check_input_hsels(self, mod: SvModule, log: Optional[list]) -> List[Finding]:
        """Rule 2: an input `hsel` / `<x>_hsel` of a structural module needs a
        reader whenever its `htrans` partner is used."""
        findings = []
        for port, pdir in mod.ports.items():
            if pdir != 'input' or not re.fullmatch(r'(?:\w+_)?hsel', port, re.I):
                continue
            partner = port[:-4] + ('HTRANS' if port[-4:].isupper() else 'htrans')
            self.checked_inputs += 1
            found, dead = self.readers(mod, port)
            real = [f for f in found if not f.startswith(UNANALYSABLE)]
            if real:
                if log is not None:
                    log.append((f'{mod.name}.{port} (input)', real[0]))
                continue
            if found:
                self.unanalysable.append(Finding(mod.name, port, partner, found, kind='input'))
                continue
            used = partner in mod.ports and self._net_used(mod, partner)
            f = Finding(mod.name, port, partner, dead or ['declared and not used'],
                        used, kind='input')
            (findings if used else self.unused_inputs).append(f)
        return findings

    def reachable(self, roots: Iterable[str]) -> List[SvModule]:
        out, todo, seen = [], list(roots), set()
        while todo:
            n = todo.pop(0)
            if n in seen or n not in self.mods:
                continue
            seen.add(n)
            m = self.mods[n]
            if not m.structural:
                continue
            out.append(m)
            todo += [i.module for i in m.instances]
        return out

    def roots(self) -> List[str]:
        return sorted(n for n, m in self.mods.items()
                      if m.structural and n not in self.parents)

    def run(self, roots: Optional[Iterable[str]] = None,
            log: Optional[list] = None) -> List[Finding]:
        """Check every structural module reachable from `roots` (default:
        every structural module nothing else instantiates). Afterwards
        self.checked, self.modules and self.unanalysable describe the run."""
        self.checked, self.unanalysable = 0, []
        self.checked_inputs, self.unused_inputs = 0, []
        self.modules = self.reachable(roots if roots else self.roots())
        findings = []
        for m in self.modules:
            findings += self.check_module(m, log)
        return findings


def check_rtl(paths: Iterable[Path], roots: Optional[Iterable[str]] = None) -> List[Finding]:
    """Run the rule over the given RTL files; `roots` limits it to the
    structural modules reachable from those module names."""
    return HselReaderLint(index_rtl(paths)).run(roots)


def check_rtl_dir(rtl_dir: Path, roots: Optional[Iterable[str]] = None) -> List[Finding]:
    return check_rtl(rtl_files(rtl_dir), roots)


def lint_rtl_dir(rtl_dir: Path, roots: Optional[Iterable[str]] = None,
                 log: Optional[list] = None) -> Tuple[List[Finding], HselReaderLint]:
    """(findings, the lint after its run: .checked, .modules, .unanalysable)."""
    lint = HselReaderLint(index_rtl(rtl_files(rtl_dir)))
    return lint.run(roots, log), lint


FIX_ADVICE = (
    "Fix each one where its target is wired: a link into a child subsystem "
    "needs the child's htrans gated with this hsel "
    "(<child>_htrans = <tgt>_hsel ? <tgt>_htrans : 2'b00; the top-level backend "
    "emits this for every generated child link, so a missing gate means a "
    "hand-edited or stale module; an unread module input hsel needs the same "
    "gate inside that module); a leaf target needs its HSEL input "
    "connected; a dangling target needs connecting or removing from the "
    "interconnect.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog='python -m soc_model.backends.hsel_lint',
        description='Fail when a bus-matrix-driven <tgt>_hsel has no reader '
                    'outside the matrix in rendered nanosoc_gen RTL.')
    ap.add_argument('paths', nargs='+', help='rtl directories and/or .sv/.v files')
    ap.add_argument('--top', action='append', default=None,
                    help='root module(s) to check from (default: every '
                         'structural module nothing else instantiates)')
    ap.add_argument('-v', '--verbose', action='store_true',
                    help='also list every hsel that passed, with its reader')
    args = ap.parse_args(argv)
    files: List[Path] = []
    for p in map(Path, args.paths):
        files += rtl_files(p) if p.is_dir() else [p]
    lint = HselReaderLint(index_rtl(files))
    log: list = []
    findings = lint.run(args.top, log)
    print(f'hsel-reader lint: {len(lint.modules)} structural module(s) checked '
          f'({", ".join(m.name for m in lint.modules)}); '
          f'{lint.checked} matrix-driven hsel(s), {lint.checked_inputs} hsel input(s)')
    if args.verbose:
        for net, reader in log:
            print(f'  ok: {net} <- {reader}')
    for u in lint.unanalysable + lint.unused_inputs:
        print(f'  NOTE (not failing): {u}')
    for f in findings:
        print(f'  ERROR: {f}')
    if findings:
        print(f'  {FIX_ADVICE}')
    print(f'hsel-reader lint: {len(findings)} finding(s), '
          f'{len(lint.unanalysable) + len(lint.unused_inputs)} note(s)')
    return 1 if findings else 0


if __name__ == '__main__':
    sys.exit(main())
