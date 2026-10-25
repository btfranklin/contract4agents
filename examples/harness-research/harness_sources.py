"""Read a local synthetic record; this tool makes no network request."""

from __future__ import annotations

import json
from pathlib import Path


def lookup(request: dict[str, object]) -> dict[str, object]:
    """Return the fixture record for the supplied typed request."""

    del request
    source = json.loads((Path(__file__).parent / "data/source.json").read_text())
    return {"answer": source["answer"], "evidence_ids": [source["source_id"]]}
