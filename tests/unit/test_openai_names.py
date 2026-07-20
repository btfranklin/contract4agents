from __future__ import annotations

import pytest

from contract4agents.adapters._openai_names import contract_tool_name, openai_tool_name


def test_openai_tool_names_round_trip_and_validate_prefixed_encodings() -> None:
    contract_name = "crm.create_note"
    encoded = openai_tool_name(contract_name)

    assert encoded == "c4a_3_crm11_create_note"
    assert contract_tool_name(encoded) == contract_name
    assert contract_tool_name("ordinary_tool") == "ordinary_tool"
    for malformed in ("c4a_bad", "c4a__bad", "c4a_x_bad", "c4a_9_short"):
        with pytest.raises(ValueError, match="not valid"):
            contract_tool_name(malformed)
