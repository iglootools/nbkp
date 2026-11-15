"""Tests for build-scripts/verifyversion.py, the guard against 0.0.0 wheels.

It is the only check between a fallback-versioned wheel and PyPI, where a
version number can never be reused.
"""

import importlib.util
import zipfile
from pathlib import Path
from types import ModuleType

import pytest
from typer.testing import CliRunner

_SCRIPT = Path(__file__).parents[2] / "build-scripts" / "verifyversion.py"


def _load_script() -> ModuleType:
    """build-scripts/ is not a package (its name has a hyphen): load by path."""
    spec = importlib.util.spec_from_file_location("verifyversion", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verifyversion = _load_script()


def _wheel(dist: Path, version: str, *, filename_version: str | None = None) -> Path:
    """A minimal wheel whose METADATA records *version*."""
    dist.mkdir(exist_ok=True)
    wheel = dist / f"nbkp-{filename_version or version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            f"nbkp-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.4\nName: nbkp\nVersion: {version}\n",
        )
    return wheel


def _verify(dist: Path) -> int:
    result = CliRunner().invoke(verifyversion.app, ["--dist-dir", str(dist)])
    return result.exit_code


class TestVerifyVersion:
    def test_fallback_version_fails(self, tmp_path: Path) -> None:
        _wheel(tmp_path, "0.0.0")
        assert _verify(tmp_path) == 1

    def test_fallback_among_good_wheels_fails(self, tmp_path: Path) -> None:
        _wheel(tmp_path, "1.2.3")
        _wheel(tmp_path, "0.0.0")
        assert _verify(tmp_path) == 1

    def test_empty_dist_dir_fails(self, tmp_path: Path) -> None:
        """A build that produced no wheel must not pass as verified."""
        assert _verify(tmp_path) == 1

    @pytest.mark.parametrize("version", ["1.0.0.dev0", "10.0.0", "0.2.1.post5.dev0"])
    def test_real_version_passes(self, tmp_path: Path, version: str) -> None:
        _wheel(tmp_path, version)
        assert _verify(tmp_path) == 0

    def test_version_read_from_metadata_not_filename(self, tmp_path: Path) -> None:
        _wheel(tmp_path, "0.0.0", filename_version="1.0.0")
        assert _verify(tmp_path) == 1

    def test_wheel_without_metadata_is_an_error(self, tmp_path: Path) -> None:
        with zipfile.ZipFile(tmp_path / "nbkp-1.0.0-py3-none-any.whl", "w") as zf:
            zf.writestr("nbkp/__init__.py", "")
        assert _verify(tmp_path) != 0
