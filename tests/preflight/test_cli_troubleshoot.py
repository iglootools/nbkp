"""Tests for `preflight troubleshoot` and the check/troubleshoot JSON outputs."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nbkp.cli import app
from nbkp.config.testkit import write_config
from nbkp.preflight import SyncError, SyncStatus
from tests.clidisplay import (
    preflight,
    runner,
    sample_all_active_sync_statuses,
    sample_all_active_vol_statuses,
    sample_config,
    sample_sentinel_only_sync_statuses,
    sample_sync_statuses,
    sample_vol_statuses,
)


class TestCheckJsonIncludesSshEndpoints:
    @patch("nbkp.cliwiring.preflight.check_all_syncs")
    def test_ssh_endpoints_in_json(
        self, mock_checks: MagicMock, tmp_path: Path
    ) -> None:
        config = sample_config()
        config_path = write_config(tmp_path, config)
        vol_s = sample_all_active_vol_statuses(config)
        mock_checks.return_value = preflight(
            vol_s, sample_all_active_sync_statuses(config, vol_s)
        )
        result = runner.invoke(
            app, ["preflight", "check", "--config", str(config_path), "-o", "json"]
        )
        data = json.loads(result.output)
        assert [s["slug"] for s in data["ssh_endpoints"]]
        assert "warnings" in data["ssh_endpoints"][0]


def _with_host_errors(statuses: dict[str, SyncStatus]) -> dict[str, SyncStatus]:
    """Mark every sync as blocked by host tool errors (fatal)."""
    return {
        slug: ss.model_copy(update={"errors": [SyncError.ENDPOINT_HOST_ERRORS]})
        for slug, ss in statuses.items()
    }


class TestTroubleshootCommand:
    @patch("nbkp.cliwiring.preflight.check_all_syncs")
    def test_json_lists_issues_and_exits_1_on_fatal(
        self, mock_checks: MagicMock, tmp_path: Path
    ) -> None:
        config = sample_config()
        config_path = write_config(tmp_path, config)
        vol_s = sample_all_active_vol_statuses(config)
        mock_checks.return_value = preflight(
            vol_s, _with_host_errors(sample_all_active_sync_statuses(config, vol_s))
        )
        result = runner.invoke(
            app,
            ["preflight", "troubleshoot", "--config", str(config_path), "-o", "json"],
        )
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["has_fatal_errors"] is True
        codes = {issue["code"] for issue in data["issues"]}
        assert "ENDPOINT_HOST_ERRORS" in codes
        assert all(issue["remediation"] for issue in data["issues"])

    @patch("nbkp.cliwiring.preflight.check_all_syncs")
    def test_inactive_only_exits_0_unless_strict(
        self, mock_checks: MagicMock, tmp_path: Path
    ) -> None:
        config = sample_config()
        config_path = write_config(tmp_path, config)
        vol_s = sample_all_active_vol_statuses(config)
        mock_checks.return_value = preflight(
            vol_s, sample_sentinel_only_sync_statuses(config, vol_s)
        )
        args = ["preflight", "troubleshoot", "--config", str(config_path)]
        assert runner.invoke(app, args).exit_code == 0
        assert runner.invoke(app, [*args, "-S", "ignore-none"]).exit_code == 1


class TestSuggestedCommands:
    @patch("nbkp.cliwiring.preflight.check_all_syncs")
    def test_check_hint_carries_config_and_endpoint_flags(
        self,
        mock_checks: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Run from the config's directory: the hint shows the path relative
        # to it, short enough not to wrap inside the panel.
        monkeypatch.chdir(tmp_path)
        config = sample_config()
        write_config(tmp_path, config)
        vol_s = sample_vol_statuses(config)
        mock_checks.return_value = preflight(vol_s, sample_sync_statuses(config, vol_s))
        result = runner.invoke(
            app, ["preflight", "check", "--config", "config.yaml", "-N", "private"]
        )
        flat = " ".join(
            line.strip(" │") for line in result.output.splitlines() if line.strip()
        )
        assert "nbkp preflight troubleshoot -c config.yaml -N private" in flat
