from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from contract4agents.cli import main
from tests.support.agentcore_harness import write_project


def test_harness_cli_writes_aws_cli_input_and_manifest(tmp_path):
    root = write_project(tmp_path / "project")
    out = tmp_path / "generated"
    result = CliRunner().invoke(
        main, ["harness-config", str(root), "--target", "agentcore_harness", "--profile", "test", "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert json.loads((out / "Analyst.harness.json").read_text())["memory"] == {"disabled": {}}
    assert json.loads((out / "manifest.json").read_text())["plan_digest"].startswith("sha256:")


def test_harness_cli_rejects_bad_target_and_unsafe_destination(tmp_path):
    root = write_project(tmp_path / "project")
    for target, out in (("missing", tmp_path / "generated"), ("agentcore_harness", root)):
        result = CliRunner().invoke(
            main, ["harness-config", str(root), "--target", target, "--profile", "test", "--out", str(out)]
        )
        assert result.exit_code != 0
    assert not (root / "Analyst.harness.json").exists()


def test_harness_research_example_runs_offline(tmp_path):
    script = Path(__file__).resolve().parents[3] / "examples/harness-research/run_demo.py"
    result = subprocess.run([sys.executable, str(script)], cwd=tmp_path, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["evidence_ids"] == ["synthetic-record-1"]
    assert "three completed checks" in output["answer"]
    trace = (tmp_path / ".contract/harness-demo/trace.jsonl").read_text()
    assert "composition.completed" in trace
    assert "The fictional Northstar project" not in trace
