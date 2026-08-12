"""Verify built wheels do not carry the dynamic-versioning fallback version.

`[tool.uv-dynamic-versioning] fallback-version` turns "no git history" into a *successful*
build that produces 0.0.0, rather than an error. The fallback is required — Renovate runs
`uv lock --upgrade-package`, which triggers a build with no git context — so nothing else
in the publish path would catch a mis-versioned artifact, and PyPI never allows a version
number to be reused once taken.

Routes into a fallback build: a shallow CI checkout without `fetch-tags`, a repository with
no tags yet, or `uv build` without `--sdist --wheel` (which builds the wheel from the sdist,
a directory with no `.git`).

Usage:
    python build-scripts/verifyversion.py              # check dist/
    python build-scripts/verifyversion.py --dist-dir b/ # check another directory
"""

from __future__ import annotations

import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

# Must stay in sync with [tool.uv-dynamic-versioning] fallback-version in pyproject.toml.
FALLBACK_VERSION = "0.0.0"

DIST_DIR = Path("dist")

console = Console()
err_console = Console(stderr=True)


@dataclass(frozen=True)
class WheelVersion:
    """The version recorded in a built wheel's own metadata."""

    wheel: Path
    version: str

    @property
    def is_fallback(self) -> bool:
        return self.version == FALLBACK_VERSION


def _metadata_version(wheel: Path) -> str:
    """Read the `Version:` field from a wheel's .dist-info/METADATA.

    Reads the metadata rather than parsing the filename on purpose: a legitimate version
    such as 1.0.0.dev0 contains "0.0.0" as a substring, so a filename glob would report a
    false positive.
    """
    with zipfile.ZipFile(wheel) as archive:
        metadata_name = next(
            (n for n in archive.namelist() if n.endswith(".dist-info/METADATA")), None
        )
        if metadata_name is None:
            raise ValueError(f"{wheel}: no .dist-info/METADATA entry")
        lines = archive.read(metadata_name).decode("utf-8").splitlines()

    version = next(
        (
            line.removeprefix("Version: ")
            for line in lines
            if line.startswith("Version: ")
        ),
        None,
    )
    if version is None:
        raise ValueError(f"{wheel}: METADATA has no Version field")
    return version


def _wheel_versions(dist_dir: Path) -> list[WheelVersion]:
    return [
        WheelVersion(wheel=wheel, version=_metadata_version(wheel))
        for wheel in sorted(dist_dir.glob("*.whl"))
    ]


app = typer.Typer(add_completion=False)


@app.command()
def main(
    dist_dir: Annotated[
        Path,
        typer.Option("--dist-dir", help="Directory containing the built wheels"),
    ] = DIST_DIR,
) -> None:
    """Fail if any wheel in dist_dir carries the fallback version."""
    versions = _wheel_versions(dist_dir)

    # An empty directory must fail rather than pass silently, or the guard is defeated by
    # the very situation it exists to catch — a build that did not produce what it should.
    if not versions:
        err_console.print(f"No wheel found in {dist_dir}/ — nothing to verify.")
        sys.exit(1)

    for found in versions:
        console.print(f"{found.wheel.name}: {found.version}")

    fallbacks = [found for found in versions if found.is_fallback]
    if fallbacks:
        err_console.print(
            f"\n{len(fallbacks)} wheel(s) carry fallback-version {FALLBACK_VERSION}, "
            "meaning git tags/history were not visible to the build:"
        )
        for found in fallbacks:
            err_console.print(f"  {found.wheel}")
        err_console.print(
            "\nCheck that the checkout has full history and tags "
            "(fetch-depth: 0, fetch-tags: true) and that the build ran "
            "`uv build --sdist --wheel`."
        )
        sys.exit(1)


if __name__ == "__main__":
    app()
