"""Verify that built Python distributions contain the PEP 561 marker."""

from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path

MARKER = "contract4agents/py.typed"


def check_package_artifacts(dist: Path) -> None:
    """Raise an error unless the wheel and source archive contain ``py.typed``."""
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1:
        raise ValueError(f"Expected exactly one wheel in {dist}, found {len(wheels)}")
    if len(sdists) != 1:
        raise ValueError(f"Expected exactly one source archive in {dist}, found {len(sdists)}")

    wheel = wheels[0]
    with zipfile.ZipFile(wheel) as archive:
        if MARKER not in archive.namelist():
            raise ValueError(f"{wheel.name} does not contain {MARKER}")

    sdist = sdists[0]
    root = sdist.name.removesuffix(".tar.gz")
    sdist_marker = f"{root}/src/{MARKER}"
    with tarfile.open(sdist) as archive:
        if sdist_marker not in archive.getnames():
            raise ValueError(f"{sdist.name} does not contain {sdist_marker}")


def main() -> None:
    dist = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dist")
    check_package_artifacts(dist)
    print(f"Package artifact check passed: {dist}")


if __name__ == "__main__":
    main()
