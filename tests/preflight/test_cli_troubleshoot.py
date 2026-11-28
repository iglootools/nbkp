"""
Tests for `preflight troubleshoot`, run against the fake NAS (``tests.nas``).

The issues and their remediation text are tested in
test_troubleshoot_render.py; these check what the command reports and its
exit code.
"""

import json
from pathlib import Path

import pytest

from nbkp.cli import app
from nbkp.config.testkit import write_config
from nbkp.remote.dispatch import build_tool_lookup_args
from tests.fakes import FakeCommandRunner, FakeRunLock, completed, fake_runtime
from tests.fixtures.cli import runner
from tests.fixtures.disks import mounted_drive_host, with_encrypted_volume
from tests.nas import healthy_nas, nas_config
from tests.preflight.fake_hosts import FAILS


def _troubleshoot(
    tmp_path: Path,
    run: FakeCommandRunner,
    *args: str,
) -> tuple[int, str]:
    config_path = write_config(tmp_path, nas_config())
    result = runner.invoke(
        app,
        ["preflight", "troubleshoot", "--config", str(config_path), *args],
        obj=fake_runtime(run=run),
    )
    return result.exit_code, result.output


class TestTroubleshootCommand:
    def test_json_lists_issues_and_exits_1_on_fatal(self, tmp_path: Path) -> None:
        rsync_missing = healthy_nas(
            FakeCommandRunner().on(*build_tool_lookup_args("rsync"), returns=FAILS),
        )

        exit_code, output = _troubleshoot(tmp_path, rsync_missing, "-o", "json")

        assert exit_code == 1
        data = json.loads(output)
        assert data["has_fatal_errors"] is True
        codes = {issue["code"] for issue in data["issues"]}
        assert "RSYNC_NOT_FOUND" in codes
        assert all(issue["remediation"] for issue in data["issues"])

    def test_inactive_only_exits_0_unless_strict(self, tmp_path: Path) -> None:
        def sentinel_missing() -> FakeCommandRunner:
            return healthy_nas(
                FakeCommandRunner().on(
                    "test",
                    "-f",
                    "/data/photos/.nbkp-src",
                    returns=FAILS,
                ),
            )

        exit_code, output = _troubleshoot(tmp_path, sentinel_missing())
        assert exit_code == 0
        assert ".nbkp-src" in output

        exit_code, _ = _troubleshoot(tmp_path, sentinel_missing(), "-S", "ignore-none")
        assert exit_code == 1

    def test_no_issues(self, tmp_path: Path) -> None:
        exit_code, output = _troubleshoot(tmp_path, healthy_nas())

        assert exit_code == 0
        assert "No issues found." in output


class TestRunLock:
    @pytest.mark.parametrize(("drive", "locked"), [(True, True), (False, False)])
    def test_troubleshoot_takes_the_lock_only_when_it_mounts_a_drive(
        self,
        *,
        drive: bool,
        locked: bool,
        tmp_path: Path,
    ) -> None:
        config = with_encrypted_volume(nas_config()) if drive else nas_config()
        config_path = write_config(tmp_path, config)
        lock = FakeRunLock()

        result = runner.invoke(
            app,
            ["preflight", "troubleshoot", "--config", str(config_path)],
            obj=fake_runtime(
                run=healthy_nas(mounted_drive_host(completed())),
                run_lock=lock,
            ),
        )

        assert result.exit_code == 0, result.output
        assert len(lock.paths) == (1 if locked else 0)
