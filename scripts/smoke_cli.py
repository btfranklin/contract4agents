"""Run the repository's contract-first CLI smoke matrix."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD_ROOT = Path(".contract/build")


@dataclass(frozen=True)
class TargetSmoke:
    name: str
    visualize: bool = False
    evaluate: bool = False
    assure: bool = False


@dataclass(frozen=True)
class ExampleSmoke:
    name: str
    targets: tuple[TargetSmoke, ...]
    compile: bool = True
    generate_python: bool = False
    self_diff: bool = False


@dataclass(frozen=True)
class SmokeStep:
    label: str
    arguments: tuple[str, ...]


EXAMPLES = (
    ExampleSmoke(
        "incident-command",
        (
            TargetSmoke("openai", visualize=True, evaluate=True, assure=True),
            TargetSmoke("strands", visualize=True, evaluate=True, assure=True),
            TargetSmoke("google_adk", visualize=True, evaluate=True, assure=True),
        ),
        generate_python=True,
        self_diff=True,
    ),
    ExampleSmoke(
        "multi-lens-research",
        (TargetSmoke("openai", visualize=True, evaluate=True),),
    ),
    ExampleSmoke(
        "market-research-brief",
        (
            TargetSmoke("openai", visualize=True, evaluate=True),
            TargetSmoke("google_adk"),
        ),
    ),
)


def build_steps() -> tuple[SmokeStep, ...]:
    steps = [SmokeStep("Show CLI help", ("--help",))]
    for example in EXAMPLES:
        root = Path("examples") / example.name
        output = BUILD_ROOT / example.name
        steps.append(SmokeStep(f"Check {example.name}", ("check", str(root))))
        for target in example.targets:
            steps.append(
                SmokeStep(
                    f"Plan {example.name} for {target.name}",
                    (
                        "plan",
                        str(root),
                        "--target",
                        target.name,
                        "--profile",
                        "test",
                        "--out",
                        str(output / _artifact_name(target.name, "plan.json")),
                    ),
                )
            )
        if example.compile:
            steps.append(
                SmokeStep(
                    f"Compile {example.name}",
                    ("compile", str(root), "--out", str(output)),
                )
            )
        if example.generate_python:
            steps.append(
                SmokeStep(
                    f"Generate Python for {example.name}",
                    (
                        "generate",
                        str(root),
                        "--target",
                        "python",
                        "--out",
                        str(output / "generated"),
                    ),
                )
            )
        for target in example.targets:
            steps.extend(_target_steps(example, target, root, output))
        if example.self_diff:
            steps.append(
                SmokeStep(
                    f"Diff {example.name} against itself",
                    (
                        "diff",
                        str(root),
                        str(root),
                        "--out",
                        str(output / "diff.json"),
                    ),
                )
            )
    return tuple(steps)


def _target_steps(
    example: ExampleSmoke,
    target: TargetSmoke,
    root: Path,
    output: Path,
) -> tuple[SmokeStep, ...]:
    steps: list[SmokeStep] = []
    common = (str(root), "--target", target.name, "--profile", "test")
    if target.visualize:
        steps.append(
            SmokeStep(
                f"Visualize {example.name} for {target.name}",
                (
                    "visualize",
                    *common,
                    "--out",
                    str(output / _artifact_name(target.name, "visualization")),
                ),
            )
        )
    eval_results = output / _artifact_name(target.name, "eval-replay.json")
    if target.evaluate:
        steps.append(
            SmokeStep(
                f"Replay {example.name} evidence for {target.name}",
                ("eval", "replay", *common, "--out", str(eval_results)),
            )
        )
    if target.assure:
        steps.append(
            SmokeStep(
                f"Assemble assurance for {example.name} on {target.name}",
                (
                    "assure",
                    *common,
                    "--eval-results",
                    str(eval_results),
                    "--out",
                    str(output / _artifact_name(target.name, "assurance")),
                ),
            )
        )
    return tuple(steps)


def _artifact_name(target: str, name: str) -> str:
    if target == "openai":
        return name
    return f"{target.replace('_', '-')}-{name}"


def main() -> None:
    steps = build_steps()
    for index, step in enumerate(steps, start=1):
        print(f"[{index}/{len(steps)}] {step.label}", flush=True)
        subprocess.run(
            (sys.executable, "-m", "contract4agents", *step.arguments),
            cwd=ROOT,
            check=True,
        )


if __name__ == "__main__":
    main()
