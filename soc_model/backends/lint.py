"""Lint backend — runs slang (sv-lang) on all design RTL files.

Parses the project's .flist files (resolving environment variables and
recursive -f includes) to collect the full set of RTL source files and
+incdir+ paths, then invokes slang to lint them.

Supports two modes:
  1. pyslang (Python bindings) — preferred, no external binary needed
  2. slang CLI fallback — uses the slang binary from PATH
"""

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import pyslang
    _HAS_PYSLANG = True
except ImportError:
    _HAS_PYSLANG = False


class FlistParser:
    """Parses Verilog filelists (.flist), resolving env vars and -f includes."""

    def __init__(self, env: Optional[Dict[str, str]] = None):
        self.env = dict(os.environ)
        if env:
            self.env.update(env)
        self.files: List[str] = []
        self.incdirs: List[str] = []
        self._visited: set = set()

    def _expand_vars(self, line: str) -> str:
        """Expand $(VAR) and $VAR references using the environment."""
        # Handle $(VAR) style
        def _repl(m):
            var = m.group(1)
            return self.env.get(var, m.group(0))
        return re.sub(r'\$\((\w+)\)', _repl, line)

    def parse(self, flist_path: str):
        """Parse a .flist file, recursively following -f includes."""
        flist_path = os.path.abspath(flist_path)
        if flist_path in self._visited:
            return
        self._visited.add(flist_path)

        if not os.path.isfile(flist_path):
            print(f"  WARNING: flist not found: {flist_path}")
            return

        flist_dir = os.path.dirname(flist_path)

        with open(flist_path) as f:
            for raw_line in f:
                line = raw_line.strip()

                # Skip empty lines and comments
                if not line or line.startswith('//'):
                    continue

                line = self._expand_vars(line)

                # -f <path> — recursive include
                if line.startswith('-f '):
                    inc_path = line[3:].strip()
                    if not os.path.isabs(inc_path):
                        inc_path = os.path.join(flist_dir, inc_path)
                    self.parse(inc_path)
                    continue

                # +incdir+ directive
                if line.startswith('+incdir+'):
                    for d in line[8:].split('+'):
                        d = d.strip()
                        if d:
                            if not os.path.isabs(d):
                                d = os.path.join(flist_dir, d)
                            self.incdirs.append(d)
                    continue

                # +libext+ and other + directives — skip
                if line.startswith('+'):
                    continue

                # -y directory — skip (library search, not direct source)
                if line.startswith('-y '):
                    continue

                # Everything else is a source file path
                if not os.path.isabs(line):
                    line = os.path.join(flist_dir, line)

                if os.path.isfile(line):
                    if line not in self.files:
                        self.files.append(line)
                else:
                    print(f"  WARNING: file not found: {line}")


class SoCLintBackend:
    """Lints design RTL using slang (sv-lang)."""

    def __init__(self, slang_bin: Optional[str] = None):
        self.slang_bin = slang_bin or shutil.which('slang') or 'slang'

    @staticmethod
    def build_env(project_dir: str) -> Dict[str, str]:
        """Build the SOCLABS_* environment variables from the project directory."""
        project_dir = os.path.abspath(project_dir)
        soc_dir = os.path.join(project_dir, 'nanosoc_m0_soc')
        tech_dir = os.path.join(soc_dir, 'nanosoc_arch_tech')
        rtl_dir = os.path.join(tech_dir, 'rtl')

        env = {
            'SOCLABS_PROJECT_DIR': project_dir,
            'SOCLABS_NANOSOC_SOC_DIR': soc_dir,
            'SOCLABS_NANOSOC_ARCH_TECH_DIR': tech_dir,
            # IP submodules
            'SOCLABS_SOCDEBUG_TECH_DIR': os.path.join(rtl_dir, 'socdebug_tech'),
            'SOCLABS_SLCOREM0_TECH_DIR': os.path.join(rtl_dir, 'slcorem0_tech'),
            'SOCLABS_SLDMA230_TECH_DIR': os.path.join(rtl_dir, 'sldma230_tech'),
            'SOCLABS_SLDMA350_TECH_DIR': os.path.join(rtl_dir, 'sldma350_tech'),
            'SOCLABS_HOSTIO4_TECH_DIR': os.path.join(rtl_dir, 'hostio4'),
        }
        return env

    def collect_rtl_files(self, build_dir: Path) -> List[Path]:
        """Collect all generated .sv and .v files under the build directory."""
        build_dir = Path(build_dir)
        files = []
        for ext in ('*.sv', '*.v'):
            files.extend(sorted(build_dir.rglob(ext)))
        return files

    def collect_from_flist(self, flist_path: str,
                           project_dir: str) -> Tuple[List[str], List[str]]:
        """Parse a filelist and return (source_files, include_dirs)."""
        env = self.build_env(project_dir)
        parser = FlistParser(env)
        parser.parse(flist_path)
        return parser.files, parser.incdirs

    def _lint_pyslang(self, rtl_files: List[str],
                      incdirs: Optional[List[str]] = None) -> Tuple[int, int, str]:
        """Lint using pyslang. Returns (error_count, warning_count, report)."""
        comp = pyslang.Compilation()
        trees = []
        for f in rtl_files:
            tree = pyslang.SyntaxTree.fromFile(str(f))
            trees.append(tree)
            comp.addSyntaxTree(tree)

        diags = comp.getAllDiagnostics()
        if not diags:
            return (0, 0, '')

        sm = trees[0].sourceManager
        report = pyslang.DiagnosticEngine.reportAll(sm, diags)
        error_count = sum(1 for d in diags if d.isError())
        warning_count = len(diags) - error_count
        return (error_count, warning_count, report)

    def _lint_cli(self, rtl_files: List[str],
                  incdirs: Optional[List[str]] = None,
                  extra_args: Optional[List[str]] = None) -> Tuple[int, str, str]:
        """Lint using slang CLI. Returns (returncode, stdout, stderr)."""
        cmd = [self.slang_bin]
        if incdirs:
            for d in incdirs:
                cmd.extend(['-I', d])
        if extra_args:
            cmd.extend(extra_args)
        cmd.extend(rtl_files)

        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=300)
            return (result.returncode, result.stdout, result.stderr)
        except (FileNotFoundError, OSError):
            return (-1, '', f"slang not found at '{self.slang_bin}'. "
                    "Install pyslang ('pip install pyslang') or the slang binary "
                    "from https://github.com/MikePopoloski/slang")
        except subprocess.TimeoutExpired:
            return (-1, '', 'slang timed out after 300 seconds.')

    def lint_and_report(self, build_dir: Path,
                        project_dir: Optional[str] = None,
                        flist_path: Optional[str] = None,
                        extra_args: Optional[List[str]] = None) -> bool:
        """Run lint and print results. Returns True if lint passed (no errors).

        If flist_path is provided, parses it to collect all design RTL files.
        Any generated files in build_dir not already in the flist are added too.
        Otherwise, falls back to scanning build_dir for generated files only.
        """
        build_dir = Path(build_dir)
        incdirs: List[str] = []

        if flist_path and project_dir:
            print(f"  Parsing filelist: {flist_path}")
            rtl_files, incdirs = self.collect_from_flist(flist_path, project_dir)
        else:
            rtl_files = [str(f) for f in self.collect_rtl_files(build_dir)]

        print(f"  Found {len(rtl_files)} RTL file(s) to lint")
        if incdirs:
            print(f"  Include directories: {len(incdirs)}")

        if not rtl_files:
            print("  No RTL files found to lint.")
            return True

        if _HAS_PYSLANG:
            print("  Using pyslang (Python bindings)")
            error_count, warning_count, report = self._lint_pyslang(rtl_files, incdirs)
            if report.strip():
                print(report)
            print(f"  {error_count} error(s), {warning_count} warning(s)")
            if error_count == 0:
                print("  Lint PASSED.")
                return True
            else:
                print("  Lint FAILED.")
                return False
        else:
            print(f"  Using slang CLI: {self.slang_bin}")
            returncode, stdout, stderr = self._lint_cli(rtl_files, incdirs, extra_args)
            if returncode == -1:
                print(f"  WARNING: {stderr}")
                return False
            if stdout.strip():
                print(stdout)
            if stderr.strip():
                print(stderr)
            if returncode == 0:
                print("  Lint PASSED.")
                return True
            else:
                print(f"  Lint FAILED — slang exited with code {returncode}.")
                return False
