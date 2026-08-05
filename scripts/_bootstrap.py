"""Make ``src/`` importable when a script is run without installing the package.

Exposes a *callable* rather than relying on import side effects, so a caller
reads as intentional and no linter sees an unused import.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def ensure_src_on_path() -> Path:
    """Prepend the repository's ``src/`` to ``sys.path`` if it is missing."""
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    return SRC
