"""Tests for nbkp.cliwiring.lock: what a command does when the run lock is held."""

import json
from pathlib import Path

import pytest
import typer

from nbkp.cliwiring.lock import run_lock_or_exit
from nbkp.cliwiring.output_format import OutputFormat
from nbkp.runlock import hold_run_lock


class TestRunLockOrExit:
    def test_a_held_lock_exits_1_before_the_block(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        path = tmp_path / "run.lock"
        entered = False
        with (
            hold_run_lock(path),
            pytest.raises(typer.Exit) as exc,
            run_lock_or_exit(hold_run_lock, path, OutputFormat.JSON),
        ):
            entered = True

        assert exc.value.exit_code == 1
        assert not entered
        error = json.loads(capsys.readouterr().out)["error"]
        assert error["reason"] == "run-in-progress"
        assert error["lock_path"] == str(path)
        assert "another nbkp command" in error["message"]

    def test_a_held_lock_is_reported_on_stderr_for_humans(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        path = tmp_path / "run.lock"
        with (
            hold_run_lock(path),
            pytest.raises(typer.Exit),
            run_lock_or_exit(hold_run_lock, path, OutputFormat.HUMAN),
        ):
            pass

        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.startswith("Error: another nbkp command")

    @pytest.mark.parametrize("only_mounting", [True, False])
    def test_no_mount_is_suggested_only_when_the_lock_is_only_for_mounting(
        self,
        *,
        only_mounting: bool,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        path = tmp_path / "run.lock"
        with (
            hold_run_lock(path),
            pytest.raises(typer.Exit),
            run_lock_or_exit(
                hold_run_lock,
                path,
                OutputFormat.JSON,
                only_mounting=only_mounting,
            ),
        ):
            pass

        message = json.loads(capsys.readouterr().out)["error"]["message"]
        assert ("--no-mount" in message) is only_mounting

    def test_not_needed_takes_no_lock(self, tmp_path: Path) -> None:
        path = tmp_path / "run.lock"
        with (
            hold_run_lock(path),
            run_lock_or_exit(hold_run_lock, path, OutputFormat.JSON, needed=False),
        ):
            pass
