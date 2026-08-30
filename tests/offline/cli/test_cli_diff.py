from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from contract4agents.cli import main
from contract4agents.ir import AgentIR, CanonicalIR, ParameterIR, parse_type_ref, semantic_id

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = ROOT / "examples" / "incident-command"


def test_cli_diff_serializes_changed_portable_defaults(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    before = tmp_path / "before"
    after = tmp_path / "after"
    before.mkdir()
    after.mkdir()

    def contract(default: object) -> CanonicalIR:
        agent_id = semantic_id("agent", "Worker")
        return CanonicalIR.create(
            agents=(
                AgentIR(
                    agent_id,
                    "Worker",
                    (
                        ParameterIR(
                            "labels",
                            parse_type_ref("map[string,string]"),
                            required=False,
                            has_default=True,
                            default=default,
                        ),
                    ),
                    parse_type_ref("string"),
                    "Report the values.",
                ),
            ),
        )

    artifacts = {
        before: SimpleNamespace(ir=contract({"z": "old", "a": "first"})),
        after: SimpleNamespace(ir=contract({"y": "new"})),
    }
    monkeypatch.setattr("contract4agents.cli.compile_project", lambda path: artifacts[path])

    first = CliRunner().invoke(main, ["diff", str(before), str(after)])
    second = CliRunner().invoke(main, ["diff", str(before), str(after)])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert json.loads(first.output) == json.loads(second.output)
    assert first.output == second.output
    change = json.loads(first.output)["contract_changes"][0]
    assert change["before"] == {"a": "first", "z": "old"}
    assert change["after"] == {"y": "new"}
