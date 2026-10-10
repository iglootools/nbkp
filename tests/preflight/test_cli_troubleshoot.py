"""Tests for `preflight troubleshoot` and the check/troubleshoot JSON outputs."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from nbkp.cli import app
from nbkp.preflight import SyncError, SyncStatus
from tests.clihelpers import (
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
    @patch("nbkp.commands.preflight.check_all_syncs")
    @patch("nbkp.commands.config.load_config")
    def test_ssh_endpoints_in_json(
        self, mock_load: MagicMock, mock_checks: MagicMock
    ) -> None:
        config = sample_config()
        mock_load.return_value = config
        vol_s = sample_all_active_vol_statuses(config)
        mock_checks.return_value = preflight(
            vol_s, sample_all_active_sync_statuses(config, vol_s)
        )
        result = runner.invoke(
            app, ["preflight", "check", "--config", "/fake.yaml", "-o", "json"]
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
    @patch("nbkp.commands.preflight.check_all_syncs")
    @patch("nbkp.commands.config.load_config")
    def test_json_lists_issues_and_exits_1_on_fatal(
        self, mock_load: MagicMock, mock_checks: MagicMock
    ) -> None:
        config = sample_config()
        mock_load.return_value = config
        vol_s = sample_all_active_vol_statuses(config)
        mock_checks.return_value = preflight(
            vol_s, _with_host_errors(sample_all_active_sync_statuses(config, vol_s))
        )
        result = runner.invoke(
            app, ["preflight", "troubleshoot", "--config", "/fake.yaml", "-o", "json"]
        )
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["has_fatal_errors"] is True
        codes = {issue["code"] for issue in data["issues"]}
        assert "ENDPOINT_HOST_ERRORS" in codes
        assert all(issue["remediation"] for issue in data["issues"])

    @patch("nbkp.commands.preflight.check_all_syncs")
    @patch("nbkp.commands.config.load_config")
    def test_inactive_only_exits_0_unless_strict(
        self, mock_load: MagicMock, mock_checks: MagicMock
    ) -> None:
        config = sample_config()
        mock_load.return_value = config
        vol_s = sample_all_active_vol_statuses(config)
        mock_checks.return_value = preflight(
            vol_s, sample_sentinel_only_sync_statuses(config, vol_s)
        )
        args = ["preflight", "troubleshoot", "--config", "/fake.yaml"]
        assert runner.invoke(app, args).exit_code == 0
        assert runner.invoke(app, [*args, "-S", "ignore-none"]).exit_code == 1


class TestSuggestedCommands:
    @patch("nbkp.commands.preflight.check_all_syncs")
    @patch("nbkp.commands.config.load_config")
    def test_check_hint_carries_config_and_endpoint_flags(
        self, mock_load: MagicMock, mock_checks: MagicMock
    ) -> None:
        config = sample_config()
        mock_load.return_value = config
        vol_s = sample_vol_statuses(config)
        mock_checks.return_value = preflight(vol_s, sample_sync_statuses(config, vol_s))
        result = runner.invoke(
            app, ["preflight", "check", "--config", "/fake.yaml", "-N", "private"]
        )
        flat = " ".join(
            line.strip(" │") for line in result.output.splitlines() if line.strip()
        )
        assert "nbkp preflight troubleshoot -c /fake.yaml -N private" in flat
