"""The CLI entry points do not load heavy dependencies only some commands need.

These modules are imported with ``lazy`` (PEP 810) so that ``nbkp --help`` and
every command that does not use them skip their import cost. A single eager
import anywhere on the startup path silently undoes that, and import-linter cannot
express it (it sees lazy and eager imports alike), so it is asserted here.

Each check runs in a fresh interpreter: the test process itself has long since
imported everything.
"""

import json
import subprocess
import sys

import pytest

DEFERRED_MODULES = [
    "docker",  # only `nbkp demo seed --docker`; also an optional extra
    "jinja2",  # only `nbkp sh`
]


def _loaded_after_import(entry_module: str) -> list[str]:
    code = (
        "import json, sys\n"
        f"import {entry_module}\n"
        f"print(json.dumps([m for m in {DEFERRED_MODULES!r} if m in sys.modules]))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("entry_module", ["nbkp.cli", "nbkp.demo"])
def test_entry_point_defers_heavy_imports(entry_module: str) -> None:
    assert _loaded_after_import(entry_module) == []
