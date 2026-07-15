from __future__ import annotations

import sys
from pathlib import Path

# ruff: noqa: E402, I001

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from examples.multi_lens_research_imports.seed import seed_research_data


if __name__ == "__main__":
    seed_research_data(Path(__file__).with_name("fixture.sqlite"))
