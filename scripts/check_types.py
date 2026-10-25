"""Check project Python and each independent example module with mypy."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _check(command: list[str], root: Path) -> None:
    result = subprocess.run(command, cwd=root, check=False)
    if result.returncode:
        raise SystemExit(result.returncode)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    command = [sys.executable, "-m", "mypy", "--config-file", str(root / "pyproject.toml")]
    _check(command, root)
    examples = root / "examples"
    packages = sorted(path for path in examples.iterdir() if (path / "__init__.py").is_file())
    if packages:
        package_args = [argument for path in packages for argument in ("-p", f"examples.{path.name}")]
        _check([*command, *package_args], root)
    for path in sorted(examples.rglob("*.py")):
        if any(path.is_relative_to(package) for package in packages):
            continue
        _check([*command, str(path)], root)


if __name__ == "__main__":
    main()
