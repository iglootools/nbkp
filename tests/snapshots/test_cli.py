"""Tests for `snapshots show` / `snapshots prune` against a fake NAS.

What the commands decide from preflight and from the snapshot operations is
tested in test_show_prune.py; these check that a command wires preflight,
the operations, the mount lifecycle and the hint together.
"""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from nbkp.cli import app
from nbkp.config import Config
from nbkp.config.testkit import write_config
from tests.fakes import FakeCommandRunner, completed, fake_runtime
from tests.fixtures.cli import runner
from tests.fixtures.disks import encrypted_volume
from tests.nas import healthy_nas, nas_config
from tests.preflight.fake_hosts import FAILS

OLD = "2026-03-01T10:00:00.000Z"
MID = "2026-03-04T10:00:00.000Z"
NEW = "2026-03-06T14:30:00.000Z"
SNAPSHOTS = "/backup/photos/snapshots"
_HINT = "nbkp preflight troubleshoot -c config.yaml -N private"


@pytest.fixture(autouse=True)
def _in_tmp_path(tmp_path: Path, chdir: Callable[[Path], None]) -> None:
    """Run every test from ``tmp_path``, where it writes ``config.yaml``.

    Commands get ``-c config.yaml``, and the troubleshoot hints they print
    show that same short relative path.
    """
    chdir(tmp_path)


def _write(tmp_path: Path, config: Config | None = None) -> None:
    write_config(tmp_path, config or nas_config(snapshots="btrfs", max_snapshots=2))


def _nas(*listings: tuple[str, ...]) -> FakeCommandRunner:
    """A healthy NAS whose snapshot listing answers each of *listings* in
    turn, with ``latest`` on the newest snapshot."""
    return healthy_nas(
        FakeCommandRunner()
        .on(
            "ls",
            SNAPSHOTS,
            returns=[completed(stdout="\n".join(names) + "\n") for names in listings],
        )
        .on(
            "readlink",
            "/backup/photos/latest",
            returns=completed(stdout=f"snapshots/{NEW}\n"),
        )
    )


def _invoke(run: FakeCommandRunner, *args: str) -> tuple[int, str]:
    result = runner.invoke(
        app, ["snapshots", *args, "-c", "config.yaml"], obj=fake_runtime(run=run)
    )
    return result.exit_code, result.output


class TestShowCommand:
    def test_json(self, tmp_path: Path) -> None:
        _write(tmp_path)

        exit_code, output = _invoke(_nas((OLD, NEW)), "show", "-o", "json")

        assert exit_code == 0
        [data] = json.loads(output)
        assert [s["name"] for s in data["snapshots"]] == [OLD, NEW]
        assert data["latest"]["name"] == NEW

    def test_human_output_renders(self, tmp_path: Path) -> None:
        """The command prints the table; its cells are tested in test_output."""
        _write(tmp_path)

        exit_code, output = _invoke(_nas((NEW,)), "show")

        assert exit_code == 0
        assert "Snapshots:" in output
        assert NEW in output


class TestPruneCommand:
    def test_json(self, tmp_path: Path) -> None:
        _write(tmp_path)
        run = _nas((OLD, MID, NEW), (MID, NEW))

        exit_code, output = _invoke(run, "prune", "-o", "json")

        assert exit_code == 0
        assert json.loads(output) == [
            {
                "sync_slug": "photos",
                "deleted": [f"{SNAPSHOTS}/{OLD}"],
                "kept": 2,
                "dry_run": False,
                "skip_reason": None,
                "error": None,
                "skipped": False,
            }
        ]
        assert ["btrfs", "subvolume", "delete", f"{SNAPSHOTS}/{OLD}"] in run.commands

    def test_dry_run(self, tmp_path: Path) -> None:
        _write(tmp_path)
        run = _nas((OLD, MID, NEW))

        exit_code, output = _invoke(run, "prune", "--dry-run", "-o", "json")

        assert exit_code == 0
        [data] = json.loads(output)
        assert (data["deleted"], data["dry_run"]) == ([f"{SNAPSHOTS}/{OLD}"], True)
        assert run.commands_starting_with("btrfs", "subvolume") == []

    def test_failed_delete_fails_without_troubleshoot_hint(
        self, tmp_path: Path
    ) -> None:
        """Troubleshoot explains preflight findings, not a failed delete."""
        _write(tmp_path)
        run = _nas((OLD, MID, NEW)).on(
            "btrfs", "subvolume", "delete", returns=completed(1, stderr="denied")
        )

        exit_code, output = _invoke(run, "prune", "-N", "private")

        assert exit_code == 1
        assert "preflight troubleshoot" not in output


class TestJsonErrors:
    """The error codes scripts match on: enum member names, not prose."""

    def test_failed_delete(self, tmp_path: Path) -> None:
        _write(tmp_path)
        run = _nas((OLD, MID, NEW)).on(
            "btrfs", "subvolume", "delete", returns=completed(1, stderr="denied")
        )

        exit_code, output = _invoke(run, "prune", "-o", "json")

        assert exit_code == 1
        assert json.loads(output)[0]["error"] == {
            "kind": "operation-failed",
            "preflight_errors": [],
            "operation": "DELETE",
            "path": f"{SNAPSHOTS}/{OLD}",
            "message": "denied; the snapshot was made read-only again",
        }

    @pytest.mark.parametrize("command", ["show", "prune"])
    def test_preflight_failure(self, command: str, tmp_path: Path) -> None:
        _write(tmp_path)
        no_snapshots_dir = healthy_nas(
            FakeCommandRunner().on("test", "-d", SNAPSHOTS, returns=FAILS)
        )

        exit_code, output = _invoke(
            no_snapshots_dir, command, "-S", "ignore-none", "-o", "json"
        )

        assert exit_code == 1
        assert json.loads(output)[0]["error"] == {
            "kind": "preflight-errors",
            "preflight_errors": ["DESTINATION_ENDPOINT_INACTIVE"],
            "operation": None,
            "path": None,
            "message": None,
        }


class TestPreflightFailure:
    """A preflight failure points at ``preflight troubleshoot``, like ``run``."""

    @pytest.mark.parametrize("command", ["show", "prune"])
    def test_suggests_troubleshoot(self, command: str, tmp_path: Path) -> None:
        _write(tmp_path)
        no_snapshots_dir = healthy_nas(
            FakeCommandRunner().on("test", "-d", SNAPSHOTS, returns=FAILS)
        )

        exit_code, output = _invoke(no_snapshots_dir, command, "-N", "private")

        assert exit_code == 1
        assert _HINT in output

    @pytest.mark.parametrize("command", ["show", "prune"])
    def test_skipped_under_ignore_all(self, command: str, tmp_path: Path) -> None:
        _write(tmp_path)
        no_snapshots_dir = healthy_nas(
            FakeCommandRunner().on("test", "-d", SNAPSHOTS, returns=FAILS)
        )

        exit_code, output = _invoke(
            no_snapshots_dir, command, "-S", "ignore-all", "-o", "json"
        )

        assert exit_code == 0
        assert json.loads(output)[0]["skip_reason"] == "preflight-errors"


class TestMountStrictness:
    @pytest.mark.parametrize(
        ("strictness", "icon"), [("ignore-inactive", "⚠"), ("ignore-none", "✗")]
    )
    def test_absent_drive_is_reported_under_the_strictness(
        self, strictness: str, icon: str, tmp_path: Path
    ) -> None:
        config = nas_config(snapshots="btrfs", max_snapshots=2)
        _write(
            tmp_path,
            config.model_copy(
                update={"volumes": {**config.volumes, "enc": encrypted_volume()}}
            ),
        )
        absent = healthy_nas(
            FakeCommandRunner().on("test", "-e", returns=FAILS, volume="enc")
        )

        _, output = _invoke(absent, "show", "-S", strictness)

        assert f"{icon} mount enc" in output


class TestUnknownSyncFilter:
    @pytest.mark.parametrize("command", ["show", "prune"])
    def test_unknown_sync_is_an_error(self, command: str, tmp_path: Path) -> None:
        _write(tmp_path)
        run = healthy_nas()

        exit_code, output = _invoke(run, command, "-o", "json", "-s", "nope")

        assert exit_code == 2
        error = json.loads(output)["error"]
        assert (error["reason"], error["names"]) == ("unknown-sync", ["nope"])
        assert run.calls == []


class TestSyncFilter:
    @pytest.mark.parametrize("command", ["show", "prune"])
    def test_only_the_selected_sync_is_checked_and_reported(
        self, command: str, tmp_path: Path
    ) -> None:
        _write(
            tmp_path, nas_config(snapshots="btrfs", max_snapshots=2, syncs=("a", "b"))
        )
        run = healthy_nas()

        exit_code, output = _invoke(run, command, "-s", "b", "-o", "json")

        assert exit_code == 0
        assert [r["sync_slug"] for r in json.loads(output)] == ["b"]
        sync_a = ("/data/a", "/backup/a")
        assert [c for c in run.commands if any(a.startswith(sync_a) for a in c)] == []
        assert any(a.startswith("/backup/b") for c in run.commands for a in c)
