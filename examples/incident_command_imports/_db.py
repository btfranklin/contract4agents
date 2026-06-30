from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def connect() -> sqlite3.Connection:
    db_path = os.environ.get("CONTRACT4AGENTS_INCIDENT_DB")
    if not db_path:
        raise RuntimeError("CONTRACT4AGENTS_INCIDENT_DB is not set")
    return sqlite3.connect(Path(db_path))
