from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

from click.testing import CliRunner

from contract4agents import compile_project, materialize
from contract4agents.cli import main
from contract4agents.eval_campaigns import CampaignConfig, FileEvalProvider, run_campaign
from contract4agents.tracing import (
    NormalizedTrace,
    TraceClosureManifest,
    write_trace_jsonl,
)

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "incident-command"


def test_cli_help_and_check() -> None:
    runner = CliRunner()

    help_result = runner.invoke(main, ["--help"])
    check_result = runner.invoke(main, ["check", str(EXAMPLE)])
    eval_help = runner.invoke(main, ["eval", "--help"])
    replay_help = runner.invoke(main, ["eval", "replay", "--help"])

    assert help_result.exit_code == 0
    assert {"assess", "assure", "compile", "diff", "eval", "generate", "plan"} <= set(help_result.output.split())
    assert "through assurance" in help_result.output
    assert check_result.exit_code == 0
    assert "passed" in check_result.output
    assert "replay" in eval_help.output
    assert "replayed evidence" in replay_help.output
    assert "--target" in replay_help.output


def test_cli_check_keeps_projects_without_target_bindings_provider_neutral(tmp_path: Path) -> None:
    (tmp_path / "agent.contract").write_text(
        'type Reply:\n    text: string\n\nagent Responder() -> Reply:\n    goal = "Respond."\n',
        encoding="utf-8",
    )

    result = CliRunner().invoke(main, ["check", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "Contract4Agents check passed" in result.output


def test_cli_check_validates_every_discovered_target_profile(tmp_path: Path) -> None:
    (tmp_path / "agent.contract").write_text(
        'type Reply:\n    text: string\n\nagent Responder() -> Reply:\n    goal = "Respond."\n',
        encoding="utf-8",
    )
    (tmp_path / "contract4agents.targets.toml").write_text(
        'schema_version = "1"\n\n'
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


def test_cli_check_and_plan_reject_unsupported_openai_binding_shape(tmp_path: Path) -> None:
    (tmp_path / "agent.contract").write_text(
        "type Reply:\n"
        "    text: string\n\n"
        "tool lookup(query: string) -> Reply:\n"
        '    description = "Look up a result."\n'
        "    side_effect = false\n\n"
        "agent Responder(query: string) -> Reply:\n"
        "    use lookup:\n"
        "        availability = enabled\n"
        "        authorization = preapproved\n"
        "        execution = provider_hosted\n"
        '    goal = "Respond."\n',
        encoding="utf-8",
    )
    (tmp_path / "contract4agents.targets.toml").write_text(
        'schema_version = "1"\n\n'
        "[targets.openai]\n"
        'adapter = "openai"\n\n'
        "[targets.openai.tools.lookup]\n"
        'provider = "openai"\n'
        'tool = "file_search"\n\n'
        "[targets.openai.profiles.test]\n"
        'default_model = "test-model"\n',
        encoding="utf-8",
    )
    runner = CliRunner()

    check_result = runner.invoke(main, ["check", str(tmp_path)])
    plan_result = runner.invoke(
        main,
        ["plan", str(tmp_path), "--target", "openai", "--profile", "test"],
    )

    assert check_result.exit_code != 0
    assert "TGT111" in check_result.output
    assert plan_result.exit_code != 0
    assert "TGT111" in plan_result.output


def test_cli_plan_resolves_model_factory_from_the_project_root(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    project = tmp_path / "project"
    project.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (project / "agent.contract").write_text(
        'type Reply:\n    text: string\n\nagent Responder() -> Reply:\n    goal = "Respond."\n',
        encoding="utf-8",
    )
    (project / "models.py").write_text(
        "def create_model(*, model, options):\n"
        "    raise AssertionError('planning must not invoke the model factory')\n",
        encoding="utf-8",
    )
    (project / "contract4agents.targets.toml").write_text(
        'schema_version = "1"\n\n'
        "[targets.strands]\n"
        'adapter = "strands"\n\n'
        "[targets.strands.profiles.test]\n"
        'default_model = "test-model"\n\n'
        "[targets.strands.profiles.test.options]\n"
        'model_factory = "models:create_model"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(elsewhere)

    result = CliRunner().invoke(
        main,
        ["plan", str(project), "--target", "strands", "--profile", "test"],
    )

    assert result.exit_code == 0, result.output
    assert '"adapter": {' in result.output
    assert '"name": "strands"' in result.output


def test_cli_contract_first_workflow(tmp_path: Path) -> None:
    runner = CliRunner()
    build = tmp_path / "build"
    generated = tmp_path / "generated"
    plan = tmp_path / "plan.json"
    eval_results = tmp_path / "eval-results.json"
    trace = tmp_path / "trace.jsonl"
    closure_path = tmp_path / "trace-closure.json"
    assurance = tmp_path / "assurance"

    assert runner.invoke(main, ["compile", str(EXAMPLE), "--out", str(build)]).exit_code == 0
    missing_target = runner.invoke(main, ["generate", str(EXAMPLE), "--out", str(generated)])
    assert missing_target.exit_code != 0
    assert "Missing option '--target'" in missing_target.output
    assert (
        runner.invoke(
            main,
            ["generate", str(EXAMPLE), "--target", "python", "--out", str(generated)],
        ).exit_code
        == 0
    )
    assert (generated / "python" / "models.py").is_file()
    assert (
        runner.invoke(
            main,
            ["plan", str(EXAMPLE), "--target", "openai", "--profile", "test", "--out", str(plan)],
        ).exit_code
        == 0
    )
    evaluated_trace, evaluated_closure = _evaluated_trace()
    write_trace_jsonl(trace, evaluated_trace)
    closure_path.write_text(TraceClosureManifest((evaluated_closure,)).to_json())

    eval_replay = runner.invoke(
        main,
        [
            "eval",
            "replay",
            str(EXAMPLE),
            "--target",
            "openai",
            "--profile",
            "test",
            "--out",
            str(eval_results),
        ],
    )
    assert eval_replay.exit_code == 0, eval_replay.output
    assert "1 passed, 0 violated, 0 unverified" in eval_replay.output

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
            "--trace-closure",
            str(closure_path),
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
            "--trace-closure",
            str(closure_path),
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
    closure = campaign.cases[0].trials[0].trace_closure
    assert trace is not None
    assert closure is not None
    return trace, closure
