from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

from click.testing import CliRunner

from contract4agents import compile_project, materialize
from contract4agents.cli import main
from contract4agents.eval_campaigns import CampaignConfig, FileEvalProvider, run_campaign
from contract4agents.tracing import NormalizedTrace, write_trace_jsonl

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "incident-command"


def test_cli_help_and_check() -> None:
    runner = CliRunner()

    help_result = runner.invoke(main, ["--help"])
    check_result = runner.invoke(main, ["check", str(EXAMPLE)])
    eval_help = runner.invoke(main, ["eval", "--help"])

    assert help_result.exit_code == 0
    assert {"assess", "assure", "compile", "diff", "eval", "generate", "plan"} <= set(
        help_result.output.split()
    )
    assert "through assurance" in help_result.output
    assert check_result.exit_code == 0
    assert "passed" in check_result.output
    assert "target profile" in eval_help.output


def test_cli_check_keeps_projects_without_target_bindings_provider_neutral(tmp_path: Path) -> None:
    (tmp_path / "agent.contract").write_text(
        "type Reply:\n"
        "    text: string\n\n"
        "agent Responder() -> Reply:\n"
        '    goal = "Respond."\n',
        encoding="utf-8",
    )

    result = CliRunner().invoke(main, ["check", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "Contract4Agents check passed" in result.output


def test_cli_check_validates_every_discovered_target_profile(tmp_path: Path) -> None:
    (tmp_path / "agent.contract").write_text(
        "type Reply:\n"
        "    text: string\n\n"
        "agent Responder() -> Reply:\n"
        '    goal = "Respond."\n',
        encoding="utf-8",
    )
    (tmp_path / "contract4agents.targets.toml").write_text(
        'schema_version = "2"\n\n'
        "[targets.alpha]\n"
        'adapter = "alpha"\n\n'
        "[targets.alpha.profiles.incomplete]\n\n"
        "[targets.beta]\n"
        'adapter = "beta"\n\n'
        "[targets.beta.profiles.production]\n"
        'default_model = "model"\n\n'
        "[targets.beta.profiles.production.agents.RemovedAgent]\n"
        'model = "stale"\n',
        encoding="utf-8",
    )

    result = CliRunner().invoke(main, ["check", str(tmp_path)])

    assert result.exit_code != 0
    assert "TGT108" in result.output
    assert "TGT109" in result.output
    assert "RemovedAgent" in result.output
    assert "Responder" in result.output


def test_cli_contract_first_workflow(tmp_path: Path) -> None:
    runner = CliRunner()
    build = tmp_path / "build"
    generated = tmp_path / "generated"
    plan = tmp_path / "plan.json"
    eval_results = tmp_path / "eval-results.json"
    trace = tmp_path / "trace.jsonl"
    assurance = tmp_path / "assurance"

    assert runner.invoke(main, ["compile", str(EXAMPLE), "--out", str(build)]).exit_code == 0
    assert runner.invoke(main, ["generate", str(EXAMPLE), "--out", str(generated)]).exit_code == 0
    assert runner.invoke(
        main,
        ["plan", str(EXAMPLE), "--target", "openai", "--profile", "test", "--out", str(plan)],
    ).exit_code == 0
    evaluated_trace = _evaluated_trace()
    write_trace_jsonl(trace, evaluated_trace)

    eval_run = runner.invoke(
        main,
        [
            "eval",
            str(EXAMPLE),
            "--target",
            "openai",
            "--profile",
            "test",
            "--out",
            str(eval_results),
        ],
    )
    assert eval_run.exit_code == 0, eval_run.output
    assert "1 passed, 0 violated, 0 unverified" in eval_run.output

    assessment = runner.invoke(
        main,
        [
            "assess",
            str(EXAMPLE),
            "--target",
            "openai",
            "--profile",
            "test",
            "--trace",
            str(trace),
        ],
    )
    assert assessment.exit_code == 0, assessment.output
    assert "assessment passed" in assessment.output

    wrong_plan = f"sha256:{'f' * 64}"
    nonconforming_trace = NormalizedTrace(
        tuple(
            replace(
                event,
                context=replace(event.context, plan_digest=wrong_plan),
            )
            for event in evaluated_trace.events
        )
    )
    nonconforming_path = tmp_path / "nonconforming.trace.jsonl"
    write_trace_jsonl(nonconforming_path, nonconforming_trace)
    rejected = runner.invoke(
        main,
        [
            "assess",
            str(EXAMPLE),
            "--target",
            "openai",
            "--profile",
            "test",
            "--trace",
            str(nonconforming_path),
        ],
    )
    assert rejected.exit_code != 0
    assert "Nonconforming normalized trace" in rejected.output

    provenance = tmp_path / "provenance.json"
    provenance.write_text(json.dumps({"source": "unit-test"}))
    assured = runner.invoke(
        main,
        [
            "assure",
            str(EXAMPLE),
            "--target",
            "openai",
            "--profile",
            "test",
            "--trace",
            str(trace),
            "--eval-results",
            str(eval_results),
            "--provenance",
            str(provenance),
            "--out",
            str(assurance),
        ],
    )
    assert assured.exit_code == 0, assured.output
    assert (assurance / "attestation.json").exists()

    diff = runner.invoke(main, ["diff", str(EXAMPLE), str(EXAMPLE)])
    assert diff.exit_code == 0
    assert '"contract_changes": []' in diff.output


def test_cli_reports_invalid_normalized_trace(tmp_path: Path) -> None:
    trace_path = tmp_path / "bad.jsonl"
    trace_path.write_text("{bad\n")

    result = CliRunner().invoke(
        main,
        [
            "assess",
            str(EXAMPLE),
            "--target",
            "openai",
            "--profile",
            "test",
            "--trace",
            str(trace_path),
        ],
    )

    assert result.exit_code != 0
    assert "Invalid normalized trace" in result.output
    assert "line 1" in result.output


def test_cli_eval_requires_provider_data(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        main,
        [
            "eval",
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


def test_cli_eval_campaign_identity_tracks_the_selected_named_profile(
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


def _evaluated_trace():  # type: ignore[no-untyped-def]
    artifacts = compile_project(EXAMPLE)
    result = materialize(EXAMPLE, "openai", "test")
    campaign = asyncio.run(
        run_campaign(
            artifacts.ir,
            result.plan,
            FileEvalProvider.load(EXAMPLE / "eval-data.json"),
            CampaignConfig("cli-test"),
        )
    )
    trace = campaign.cases[0].trials[0].trace
    assert trace is not None
    return trace
