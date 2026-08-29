from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from contract4agents.cli import main

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "incident-command"


def test_cli_eval_replay_requires_provider_data(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        main,
        [
            "eval",
            "replay",
            str(EXAMPLE),
            "--target",
            "openai",
            "--profile",
            "test",
            "--data",
            str(tmp_path / "missing.json"),
        ],
    )

    assert result.exit_code != 0
    assert "Could not load eval data" in result.output


def test_cli_eval_removed_ambiguous_command_form() -> None:
    result = CliRunner().invoke(
        main,
        [
            "eval",
            str(EXAMPLE),
            "--target",
            "openai",
            "--profile",
            "test",
        ],
    )

    assert result.exit_code != 0
    assert "No such command" in result.output


def test_cli_eval_replay_identity_tracks_the_selected_named_profile(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    runner = CliRunner()
    payloads: dict[str, dict[str, object]] = {}

    async def fake_run_campaign(ir, plan, provider, config):  # type: ignore[no-untyped-def]
        del ir, provider

        class FakeReport:
            threshold_results = ()
            regression_results = ()
            summary = type(
                "Summary",
                (),
                {
                    "rates": type(
                        "Rates",
                        (),
                        {"passed": 1, "violated": 0, "unverified": 0, "total": 1},
                    )()
                },
            )()

            def to_dict(self) -> dict[str, object]:
                return {
                    "campaign_id": config.campaign_id,
                    "plan_digest": plan.plan_digest,
                    "profile": plan.profile,
                    "target": plan.target,
                }

        return FakeReport()

    monkeypatch.setattr("contract4agents.cli.run_campaign", fake_run_campaign)

    for profile in ("test", "production"):
        output = tmp_path / f"{profile}.json"
        result = runner.invoke(
            main,
            [
                "eval",
                "replay",
                str(EXAMPLE),
                "--target",
                "openai",
                "--profile",
                profile,
                "--out",
                str(output),
            ],
        )
        assert result.exit_code == 0, result.output
        payloads[profile] = json.loads(output.read_text(encoding="utf-8"))

    assert payloads["test"]["campaign_id"] == "openai:test"
    assert payloads["production"]["campaign_id"] == "openai:production"
    assert payloads["test"]["profile"] == "test"
    assert payloads["production"]["profile"] == "production"
    assert payloads["test"]["plan_digest"] != payloads["production"]["plan_digest"]
