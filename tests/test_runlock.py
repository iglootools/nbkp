"""Tests for nbkp.runlock: where the run lock lives, and holding it."""

from pathlib import Path

import pytest

from nbkp.runlock import (
    RunLockError,
    RunLockErrorKind,
    hold_run_lock,
    is_run_lock_needed,
    resolve_run_lock_path,
)
from tests.fakes import BodyError

_HOME = Path("/home/alice")


class TestResolveRunLockPath:
    def test_under_xdg_state_home(self) -> None:
        path = resolve_run_lock_path({"XDG_STATE_HOME": "/var/state"}, _HOME)
        assert path == Path("/var/state/nbkp/run.lock")

    @pytest.mark.parametrize("environ", [{}, {"XDG_STATE_HOME": ""}])
    def test_falls_back_to_local_state_when_unset(
        self,
        environ: dict[str, str],
    ) -> None:
        path = resolve_run_lock_path(environ, _HOME)
        assert path == _HOME / ".local/state/nbkp/run.lock"

    def test_ignores_a_relative_xdg_state_home(self) -> None:
        path = resolve_run_lock_path({"XDG_STATE_HOME": "state"}, _HOME)
        assert path == _HOME / ".local/state/nbkp/run.lock"


class TestHoldRunLock:
    def test_creates_the_lock_file_and_its_directory(self, tmp_path: Path) -> None:
        path = tmp_path / "state" / "nbkp" / "run.lock"
        with hold_run_lock(path):
            assert path.is_file()

    def test_a_second_holder_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "run.lock"
        with (
            hold_run_lock(path),
            pytest.raises(RunLockError) as exc,
            hold_run_lock(path),
        ):
            pass
        assert exc.value.kind is RunLockErrorKind.RUN_IN_PROGRESS
        assert exc.value.path == path

    def test_is_released_when_the_block_exits(self, tmp_path: Path) -> None:
        path = tmp_path / "run.lock"
        with hold_run_lock(path):
            pass
        with hold_run_lock(path):
            pass

    def test_is_released_when_the_block_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "run.lock"
        with pytest.raises(BodyError), hold_run_lock(path):
            raise BodyError
        with hold_run_lock(path):
            pass

    def test_unavailable_when_the_file_cannot_be_created(self, tmp_path: Path) -> None:
        (tmp_path / "state").write_text("not a directory")
        path = tmp_path / "state" / "nbkp" / "run.lock"
        with pytest.raises(RunLockError) as exc, hold_run_lock(path):
            pass
        assert exc.value.kind is RunLockErrorKind.LOCK_UNAVAILABLE
        assert exc.value.detail


class TestIsRunLockNeeded:
    @pytest.mark.parametrize(
        ("writes", "mount", "umount", "needed"),
        [
            (True, False, False, True),
            (False, True, True, True),
            (False, True, False, True),
            (False, False, True, True),
            (False, False, False, False),
        ],
    )
    def test_only_a_command_that_neither_writes_mounts_nor_umounts_skips_it(
        self,
        *,
        writes: bool,
        mount: bool,
        umount: bool,
        needed: bool,
    ) -> None:
        assert is_run_lock_needed(writes=writes, mount=mount, umount=umount) is needed
