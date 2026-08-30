from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts.check_package_artifacts import MARKER, check_package_artifacts


def test_check_package_artifacts_accepts_marker_in_wheel_and_sdist(tmp_path: Path) -> None:
    wheel = tmp_path / "contract4agents-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(MARKER, "")

    sdist = tmp_path / "contract4agents-1.0.0.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        marker = tarfile.TarInfo(f"contract4agents-1.0.0/src/{MARKER}")
        marker.size = 0
        archive.addfile(marker)

    check_package_artifacts(tmp_path)


def test_check_package_artifacts_rejects_missing_marker(tmp_path: Path) -> None:
    wheel = tmp_path / "contract4agents-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w"):
        pass

    sdist = tmp_path / "contract4agents-1.0.0.tar.gz"
    with tarfile.open(sdist, "w:gz"):
        pass

    with pytest.raises(ValueError, match="does not contain contract4agents/py.typed"):
        check_package_artifacts(tmp_path)
