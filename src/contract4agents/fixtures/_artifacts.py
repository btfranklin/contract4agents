"""Fixture artifact verification helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from contract4agents.compiler import CompilerArtifacts
from contract4agents.fixtures._models import FixtureArtifactError


def verify_fixture_artifacts(metadata: dict[str, Any], artifacts: CompilerArtifacts, build_dir: Path) -> list[str]:
    expected = metadata.get("expected", {})
    checks: list[str] = []
    _expect_set("agents", expected.get("agents", []), artifacts["manifests"])
    checks.append("expected agents present")
    _expect_set("types", expected.get("types", []), artifacts["schemas"])
    checks.append("expected schemas present")
    if len(artifacts["evals"]) != expected.get("eval_count"):
        raise FixtureArtifactError("unexpected eval artifact count")
    checks.append("expected eval count present")
    if len(artifacts["monitors"]) != expected.get("monitor_count"):
        raise FixtureArtifactError("unexpected monitor artifact count")
    checks.append("expected monitor count present")
    generated_files = [
        build_dir / "schemas" / f"{metadata['output_type']}.json",
        build_dir / "manifests" / f"{metadata['entry_agent']}.json",
        build_dir / "instructions" / f"{metadata['entry_agent']}.md",
        build_dir / "evals" / "evals.json",
        build_dir / "monitors" / "monitors.json",
        build_dir / "adapters" / "capability-matrix.json",
        build_dir / "docs" / "summary.md",
    ]
    missing_files = [str(path) for path in generated_files if not path.exists()]
    if missing_files:
        raise FixtureArtifactError(f"missing generated files: {missing_files}")
    checks.append("expected generated files present")
    manifests = artifacts["manifests"]
    all_tools = {
        tool["name"]: tool["permission"]
        for manifest in manifests.values()
        for tool in manifest.get("tools", [])
    }
    _expect_set("tools", expected.get("tools", []), all_tools)
    _expect_permissions("tool permissions", expected.get("tool_permissions", {}), all_tools)
    checks.append("expected tools and permissions present")
    coordinator = manifests[metadata["entry_agent"]]
    datasource_artifacts = {item["name"]: item for item in coordinator["datasources"]}
    _expect_set("datasources", expected.get("datasources", []), datasource_artifacts)
    checks.append("expected coordinator datasources present")
    _expect_adapter_capabilities(expected.get("adapter_capabilities", {}), artifacts["adapter_capability_matrix"])
    if expected.get("adapter_capabilities"):
        checks.append("expected adapter capabilities present")
    return checks


def _expect_set(label: str, expected: list[str], actual: dict[str, Any]) -> None:
    missing = sorted(set(expected) - set(actual))
    if missing:
        raise FixtureArtifactError(f"missing {label}: {missing}")


def _expect_permissions(label: str, expected: dict[str, str], actual: dict[str, str]) -> None:
    mismatches = {
        name: {"expected": permission, "actual": actual.get(name)}
        for name, permission in expected.items()
        if actual.get(name) != permission
    }
    if mismatches:
        raise FixtureArtifactError(f"unexpected {label}: {mismatches}")


def _expect_adapter_capabilities(expected: dict[str, dict[str, str]], actual: dict[str, Any]) -> None:
    for adapter_name, capabilities in expected.items():
        adapter = actual.get(adapter_name, {})
        for capability_name, status in capabilities.items():
            actual_status = adapter.get(capability_name, {}).get("status")
            if actual_status != status:
                raise FixtureArtifactError(
                    f"unexpected adapter capability {adapter_name}.{capability_name}: "
                    f"expected {status}, got {actual_status}"
                )


__all__ = ["verify_fixture_artifacts"]
