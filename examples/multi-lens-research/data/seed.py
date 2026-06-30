from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from examples.multi_lens_research_imports.harness import seed_multi_lens_research  # noqa: E402

if __name__ == "__main__":
    seed_multi_lens_research(Path(__file__).with_name("fixture.sqlite"))
