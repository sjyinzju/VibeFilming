"""Repository-local convenience wrapper for ``python scripts/run_mock.py``."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from movie_agent.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
