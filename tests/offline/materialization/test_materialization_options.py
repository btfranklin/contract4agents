from __future__ import annotations

from contract4agents.ir import FrozenMap, freeze_json
from contract4agents.materialization._options import thaw_mapping


def test_thaw_mapping_recursively_returns_ordinary_json_containers() -> None:
    frozen = freeze_json(
        {
            "retry": {"max_retries": 1, "backoff": {"initial_delay": 0.25}},
            "entries": [{"enabled": True}],
        }
    )
    assert isinstance(frozen, FrozenMap)

    thawed = thaw_mapping(frozen)

    assert thawed == {
        "retry": {"max_retries": 1, "backoff": {"initial_delay": 0.25}},
        "entries": [{"enabled": True}],
    }
    assert isinstance(thawed["retry"], dict)
    assert isinstance(thawed["entries"], list)
    assert isinstance(thawed["entries"][0], dict)  # type: ignore[index]
    assert isinstance(frozen["retry"], FrozenMap)
