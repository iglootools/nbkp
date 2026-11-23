"""
Importing core code loads neither Rich nor Typer.

Testkits and the demo's seeding count as core code: they ship in the wheel and
``nbkp demo`` imports them, so only their ``cli`` packages may load Typer.

Presentation lives in each package's ``output`` modules, which core code never
imports: display code imports them explicitly. The import-linter contracts let
``output`` modules use Rich, so they cannot see a core module importing its own
``output`` (and through it Rich); this asserts the outcome instead.

Packages re-export most of their API with ``lazy`` imports, so importing a
package alone proves little: every core module of the package is imported.
Each package is checked in a fresh interpreter, since the test process itself
has long since imported everything.
"""

import importlib
import json
import pkgutil
import subprocess
import sys

import pytest

import nbkp

PRESENTATION_MODULES = ["rich", "typer"]

# Everything else directly under nbkp is core.
NON_CORE_PACKAGES = {"cli", "clidisplay", "cliwiring"}

# Subpackages and modules of a core package that are not core code.
NON_CORE_PARTS = {"cli", "output"}

CORE_PACKAGES = sorted(
    m.name
    for m in pkgutil.iter_modules(nbkp.__path__)
    if m.name not in NON_CORE_PACKAGES
)


def _core_modules(package: str) -> list[str]:
    root = importlib.import_module(f"nbkp.{package}")
    submodules = (
        m.name
        for m in pkgutil.walk_packages(
            getattr(root, "__path__", []),
            f"{root.__name__}.",
        )
    )
    return [
        root.__name__,
        *sorted(
            name for name in submodules if NON_CORE_PARTS.isdisjoint(name.split("."))
        ),
    ]


def _loaded_after_import(modules: list[str]) -> list[str]:
    code = (
        "import importlib, json, sys\n"
        f"for m in {modules!r}: importlib.import_module(m)\n"
        f"print(json.dumps([m for m in {PRESENTATION_MODULES!r}"
        " if m in sys.modules]))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("package", CORE_PACKAGES)
def test_core_package_does_not_load_presentation(package: str) -> None:
    assert _loaded_after_import(_core_modules(package)) == []


def test_core_modules_are_discovered() -> None:
    assert {"config", "demo", "preflight", "sync"} <= set(CORE_PACKAGES)
    assert "nbkp.sync.results" in _core_modules("sync")
    assert "nbkp.sync.testkit.seed" in _core_modules("sync")
    assert "nbkp.demo.seed" in _core_modules("demo")
    assert "nbkp.sync.output" not in _core_modules("sync")
    assert "nbkp.demo.cli" not in _core_modules("demo")
