"""Report formatter for debug findings — coloured terminal text and markdown."""

import sys
from .analyzers.fault_detector import Finding


# ANSI colour codes
class _C:
    RED = '\033[91m'
    YELLOW = '\033[93m'
    GREEN = '\033[92m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    RESET = '\033[0m'


_SEVERITY_COLOUR = {
    Finding.FATAL: _C.RED + _C.BOLD,
    Finding.ERROR: _C.RED,
    Finding.WARN:  _C.YELLOW,
    Finding.INFO:  _C.GREEN,
}


class Report:
    """Collects sections and findings, then renders a formatted report."""

    def __init__(self, testname='', sim_dir='', use_colour=True, markdown=False):
        self.testname = testname
        self.sim_dir = sim_dir
        self.use_colour = use_colour and not markdown
        self.markdown = markdown
        self._sections = []
        self._findings = []
        self.adp_result = None   # AdpOutput or None, set by analyze()
        self.artifacts = None    # SimArtifacts, set by analyze()

    def add_section(self, title, lines=None):
        """Add a report section with optional detail lines."""
        self._sections.append((title, lines or []))

    def add_findings(self, findings):
        """Add findings from an analyser."""
        self._findings.extend(findings)

    def _c(self, code, text):
        if self.use_colour:
            return f'{code}{text}{_C.RESET}'
        return text

    def render(self):
        """Render the full report as a string."""
        if self.markdown:
            return self._render_markdown()
        return self._render_text()

    def _render_text(self):
        lines = []
        lines.append(self._c(_C.BOLD + _C.CYAN, '=' * 60))
        lines.append(self._c(_C.BOLD, f'  NanoSoC Simulation Debug Report'))
        lines.append(self._c(_C.DIM, f'  Test: {self.testname}  |  Dir: {self.sim_dir}'))
        lines.append(self._c(_C.BOLD + _C.CYAN, '=' * 60))
        lines.append('')

        for title, detail_lines in self._sections:
            lines.append(self._c(_C.BOLD, f'--- {title} ---'))
            for dl in detail_lines:
                lines.append(f'  {dl}')
            lines.append('')

        # Separate findings by severity
        fatal_error = [f for f in self._findings if f.severity in (Finding.FATAL, Finding.ERROR)]
        warnings = [f for f in self._findings if f.severity == Finding.WARN]
        info = [f for f in self._findings if f.severity == Finding.INFO]

        lines.append(self._c(_C.BOLD, '--- Findings ---'))

        if not self._findings:
            lines.append(self._c(_C.GREEN, '  No issues found'))
        else:
            for f in fatal_error + warnings + info:
                colour = _SEVERITY_COLOUR.get(f.severity, '')
                tag = f'[{f.severity}]'
                lines.append(self._c(colour, f'  {tag} {f.title}'))
                for d in f.details:
                    lines.append(f'    {d}')

                # Show instruction context for FATAL findings
                if f.severity == Finding.FATAL and f.context:
                    lines.append(f'    Last instructions before fault:')
                    for evt in f.context[-10:]:
                        if evt.event_type in ('IT', 'IS'):
                            lines.append(
                                f'      {evt.timestamp_ns:>10} ns  '
                                f'{evt.event_type} 0x{evt.pc:08X}  {evt.disasm or ""}'
                            )
                        elif evt.event_type in ('MW', 'MR'):
                            direction = 'W' if evt.mem_write else 'R'
                            data = f'0x{evt.mem_data:08X}' if evt.mem_data is not None else 'xxxxxxxx'
                            lines.append(
                                f'      {evt.timestamp_ns:>10} ns  '
                                f'M{direction} 0x{evt.mem_addr:08X} = {data}'
                            )
                lines.append('')

        # Summary
        lines.append(self._c(_C.BOLD + _C.CYAN, '-' * 60))
        n_fatal = len([f for f in self._findings if f.severity == Finding.FATAL])
        n_error = len([f for f in self._findings if f.severity == Finding.ERROR])
        n_warn = len([f for f in self._findings if f.severity == Finding.WARN])

        if n_fatal > 0:
            lines.append(self._c(_C.RED + _C.BOLD,
                f'  RESULT: FAIL ({n_fatal} fatal, {n_error} error, {n_warn} warning)'))
        elif n_error > 0:
            lines.append(self._c(_C.RED,
                f'  RESULT: FAIL ({n_error} error, {n_warn} warning)'))
        elif n_warn > 0:
            lines.append(self._c(_C.YELLOW,
                f'  RESULT: PASS with warnings ({n_warn})'))
        else:
            lines.append(self._c(_C.GREEN + _C.BOLD, '  RESULT: PASS'))
        lines.append('')

        return '\n'.join(lines)

    def _render_markdown(self):
        lines = []
        lines.append(f'# NanoSoC Simulation Debug Report')
        lines.append(f'**Test:** {self.testname} | **Dir:** `{self.sim_dir}`')
        lines.append('')

        for title, detail_lines in self._sections:
            lines.append(f'## {title}')
            for dl in detail_lines:
                lines.append(f'- {dl}')
            lines.append('')

        lines.append('## Findings')
        if not self._findings:
            lines.append('No issues found.')
        else:
            for f in self._findings:
                icon = {'FATAL': '🔴', 'ERROR': '🔴', 'WARN': '🟡', 'INFO': '🟢'}.get(f.severity, '')
                lines.append(f'### {icon} [{f.severity}] {f.title}')
                for d in f.details:
                    lines.append(f'- {d}')
                if f.context:
                    lines.append('```')
                    for evt in f.context[-10:]:
                        if evt.event_type in ('IT', 'IS'):
                            lines.append(f'{evt.timestamp_ns:>10} ns  {evt.event_type} 0x{evt.pc:08X}  {evt.disasm or ""}')
                    lines.append('```')
                lines.append('')

        return '\n'.join(lines)

    # ------------------------------------------------------------------
    # Structured output
    # ------------------------------------------------------------------
    def verdict(self):
        """Return (verdict, reason) with verdict 'PASS' or 'FAIL'.

        FAIL when any FATAL/ERROR finding exists, when no simulation output
        was found at all, or when the firmware never printed ``** TEST PASSED **``.
        (The text RESULT line is unchanged and is more lenient.)
        """
        sev = [f.severity for f in self._findings]
        if Finding.FATAL in sev or Finding.ERROR in sev:
            return 'FAIL', 'fatal or error findings present'
        adp = self.adp_result
        if adp is None:
            return 'FAIL', 'no simulation log found'
        if adp.test_result == 'FAILED':
            return 'FAIL', 'firmware reported TEST FAILED'
        if adp.test_result != 'PASSED':
            return 'FAIL', 'no ** TEST PASSED ** marker in the simulation log'
        return 'PASS', 'TEST PASSED marker seen, no fatal or error findings'

    def to_dict(self):
        """Machine-readable form of the report (JSON-serialisable)."""
        verdict, reason = self.verdict()
        counts = {s: 0 for s in (Finding.FATAL, Finding.ERROR, Finding.WARN, Finding.INFO)}
        for f in self._findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        adp = self.adp_result
        outcome = None
        if adp is not None:
            outcome = {
                'banner': adp.banner,
                'remap': adp.remap,
                'test_result': adp.test_result,
                'test_ended': adp.test_ended,
                'sim_time_ns': adp.sim_time_ns,
                'garbled': adp.garbled,
                'firmware_lines': list(adp.firmware_lines),
            }
        findings = []
        for f in self._findings:
            d = {'severity': f.severity, 'title': f.title, 'details': list(f.details)}
            d.update(getattr(f, 'data', None) or {})
            if f.context:
                d['context'] = [self._event_dict(e) for e in f.context[-10:]]
            findings.append(d)
        return {
            'verdict': verdict,
            'reason': reason,
            'testname': self.testname,
            'sim_dir': self.sim_dir,
            'counts': counts,
            'artifacts': self.artifacts.summary() if self.artifacts else None,
            'outcome': outcome,
            'sections': [{'title': t, 'lines': list(ls)} for t, ls in self._sections],
            'findings': findings,
        }

    @staticmethod
    def _event_dict(evt):
        d = {'time_ns': evt.timestamp_ns, 'type': evt.event_type}
        if evt.event_type in ('IT', 'IS'):
            d['pc'] = f'0x{evt.pc:08X}'
            d['disasm'] = evt.disasm or ''
        elif evt.event_type in ('MW', 'MR'):
            d['addr'] = f'0x{evt.mem_addr:08X}'
            d['data'] = f'0x{evt.mem_data:08X}' if evt.mem_data is not None else None
        return d
