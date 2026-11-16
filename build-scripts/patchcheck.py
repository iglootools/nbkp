"""Fail when a test replaces code or process state outside the documented exceptions.

Tests inject fakes through parameters instead of patching (docs/guidelines.md,
Testing). Ruff bans ``unittest.mock`` (TID251), but cannot see ``monkeypatch``,
a fixture parameter rather than an import, so this script parses every test
file and reports each ``monkeypatch.setattr`` / ``delattr`` / ``setitem`` /
``delitem`` / ``setenv`` / ``delenv`` call in a file that is not in ALLOWED.
An ALLOWED file that no longer makes such a call is reported too, so the list
stays the exact set of exceptions.

Only calls on a name spelled ``monkeypatch`` (the fixture) are seen: a test
that renames it or uses ``MonkeyPatch.context()`` escapes the check.

Usage:
    python build-scripts/patchcheck.py
"""

import ast
import sys
from pathlib import Path

TESTS = Path("tests")

GUARDED = frozenset({"setattr", "delattr", "setitem", "delitem", "setenv", "delenv"})

# Each entry is one of the exceptions listed in docs/guidelines.md (Testing),
# with its category there.
ALLOWED: dict[str, str] = {
    "tests/conftest.py": "(c) the default ~/.ssh/config path, kept off the developer's",
    "tests/credentials/test_credentials.py": "(b) the keyring extra, simulated as missing",
    "tests/remote/test_host_key_verification.py": "(c) HOME, read by Paramiko",
    "tests/snapshots/test_common.py": "(b) Path.symlink_to failing mid-update",
}


def _guarded_calls(path: Path) -> list[int]:
    """Line numbers of the guarded ``monkeypatch`` calls in *path*."""
    tree = ast.parse(path.read_text(), str(path))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in GUARDED
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "monkeypatch"
    ]


def main() -> int:
    found = {
        path.as_posix(): lines
        for path in sorted(TESTS.rglob("*.py"))
        if (lines := _guarded_calls(path))
    }
    unexpected = [
        f"{path}:{line}"
        for path, lines in found.items()
        if path not in ALLOWED
        for line in lines
    ]
    stale = [path for path in ALLOWED if path not in found]
    for location in unexpected:
        print(
            f"{location}: monkeypatch replaces code or process state", file=sys.stderr
        )
    for path in stale:
        print(f"{path}: listed in ALLOWED but no longer patches", file=sys.stderr)
    if unexpected or stale:
        print(
            "Inject a fake instead (docs/guidelines.md, Testing), or document a"
            " new exception there and in ALLOWED (build-scripts/patchcheck.py).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
