# Copyright 2026, SoC Labs (www.soclabs.org)
"""SoC Configurator — local web app for composing/validating a SoC and
running the build flow (roadmap doc 06a).

Layers:
  * :mod:`soc_model.gui_service` / :mod:`soc_model.completeness` — pure
    Python core (no web deps), fully covered by the unit suite.
  * :mod:`.project` — the per-repo adapter (``configurator.yaml``).
  * :mod:`.jobs` — async staged build runner with line streaming.
  * :mod:`.app` — the FastAPI shell (optional dependency; install with
    ``pip install nanosoc-gen[configurator]``).

Usage::

    python -m soc_model.configurator serve --project <repo> [--port 8090]
    python -m soc_model.configurator check <top.yaml> --lib-dir <dir> ...
"""
