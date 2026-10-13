"""Output backends for the SoC Model tool.

Importing this package fires the @register_backend decorators for the
registry-driven leaf backends (roadmap doc 02, M3). Keep these imports
explicit and greppable — no plugin auto-discovery.
"""

# Registry-driven leaf backends (M2/M3). Each import triggers @register_backend.
from . import sram  # noqa: F401
from . import text  # noqa: F401
from . import python  # noqa: F401
from . import html  # noqa: F401
