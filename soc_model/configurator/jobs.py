# Copyright 2026, SoC Labs (www.soclabs.org)
"""Staged build-pipeline runner (roadmap doc 06a §6).

Pure asyncio — no web-framework imports — so the runner is unit-testable.
One job at a time; each stage is a shell command run in its own process
group (cancellable), with stdout/stderr merged and streamed line-by-line to
a listener callback (the FastAPI layer forwards these to the WebSocket hub).

A stage failing (non-zero exit) stops the pipeline; later stages are marked
``skipped``.
"""

import asyncio
import os
import signal
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class Stage:
    name: str
    cmd: str                       # run via the shell (adapter-defined, not user input)
    cwd: str = '.'
    env: Optional[Dict[str, str]] = None   # overlaid on os.environ
    env_script: Optional[str] = None       # sourced first (e.g. set_env.sh)

    def shell_command(self) -> str:
        if self.env_script:
            return f'source {self.env_script} >/dev/null 2>&1; {self.cmd}'
        return self.cmd


@dataclass
class StageResult:
    name: str
    status: str = 'pending'        # pending|running|ok|failed|skipped|cancelled
    rc: Optional[int] = None
    seconds: float = 0.0


@dataclass
class Job:
    stages: List[Stage]
    results: List[StageResult] = field(default_factory=list)
    status: str = 'pending'        # pending|running|ok|failed|cancelled
    started: float = 0.0
    finished: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            'status': self.status,
            'started': self.started,
            'finished': self.finished,
            'stages': [vars(r) for r in self.results],
        }


class JobRunner:
    """Runs one Job at a time, streaming output lines to a listener.

    ``listener(event: dict)`` receives::

        {type: 'stage',  stage, status}
        {type: 'line',   stage, line}
        {type: 'job',    status}
    """

    def __init__(self, listener: Optional[Callable[[Dict[str, Any]], None]] = None):
        self._listener = listener or (lambda e: None)
        self._lock = asyncio.Lock()
        self.current: Optional[Job] = None
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._cancelled = False

    @property
    def busy(self) -> bool:
        return self.current is not None and self.current.status == 'running'

    def _emit(self, **event):
        try:
            self._listener(event)
        except Exception:
            pass  # a broken listener must never kill the build

    async def run(self, stages: List[Stage]) -> Job:
        if self.busy:
            raise RuntimeError('a job is already running')
        async with self._lock:
            job = Job(stages=stages,
                      results=[StageResult(name=s.name) for s in stages])
            self.current = job
            self._cancelled = False
            job.status = 'running'
            job.started = time.time()
            self._emit(type='job', status='running')
            failed = False
            for stage, result in zip(stages, job.results):
                if failed or self._cancelled:
                    result.status = 'skipped'
                    self._emit(type='stage', stage=stage.name, status='skipped')
                    continue
                result.status = 'running'
                self._emit(type='stage', stage=stage.name, status='running')
                t0 = time.time()
                rc = await self._run_stage(stage)
                result.seconds = round(time.time() - t0, 2)
                result.rc = rc
                if self._cancelled:
                    result.status = 'cancelled'
                elif rc == 0:
                    result.status = 'ok'
                else:
                    result.status = 'failed'
                    failed = True
                self._emit(type='stage', stage=stage.name,
                           status=result.status)
            job.status = ('cancelled' if self._cancelled else
                          ('failed' if failed else 'ok'))
            job.finished = time.time()
            self._emit(type='job', status=job.status)
            return job

    async def _run_stage(self, stage: Stage) -> int:
        env = dict(os.environ)
        if stage.env:
            env.update(stage.env)
        proc = await asyncio.create_subprocess_shell(
            stage.shell_command(),
            cwd=stage.cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            executable='/bin/bash',
            start_new_session=True,      # own process group -> clean cancel
        )
        self._proc = proc
        try:
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                self._emit(type='line', stage=stage.name,
                           line=line.decode('utf-8', 'replace').rstrip('\n'))
            return await proc.wait()
        finally:
            self._proc = None

    def cancel(self):
        """Terminate the running stage's whole process group."""
        self._cancelled = True
        proc = self._proc
        if proc is not None and proc.returncode is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
