from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from examples.incident_command_imports.harness import seed_incident_command  # noqa: E402

if __name__ == "__main__":
    seed_incident_command(Path(__file__).with_name("fixture.sqlite"))
