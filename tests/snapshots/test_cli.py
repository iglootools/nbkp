"""Tests for nbkp snapshots CLI commands."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from nbkp.cli import app
from nbkp.commands.mount import managed_mount
from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    LocalVolume,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.fsprotocol import Snapshot
from nbkp.policy import Strictness
from nbkp.preflight import (
    DestinationEndpointError,
    SyncError,
    SyncStatus,
    VolumeStatus,
)
from nbkp.snapshots.errors import SnapshotOp, SnapshotOperationError
from tests.clihelpers import (
    dst_ep_status,
    localhost_ssh_status,
    preflight,
    runner,
    sample_all_active_sync_statuses,
    sample_all_active_vol_statuses,
    sample_config,
    src_ep_status,
    vol_status,
)

# ── Snapshot-specific config helpers ─────────────────────────


def _prune_config() -> Config:
    src = LocalVolume(slug="src", path="/src")
    dst = LocalVolume(slug="dst", path="/dst")
    ep_src = SyncEndpoint(slug="ep-src", volume="src")
    ep_dst = SyncEndpoint(
        slug="ep-dst",
        volume="dst",
        btrfs_snapshots=BtrfsSnapshotConfig(enabled=True, max_snapshots=3),
    )
    sync = SyncConfig(
        slug="s1",
        source="ep-src",
        destination="ep-dst",
    )
    return Config(
        volumes={"src": src, "dst": dst},
        sync_endpoints={"ep-src": ep_src, "ep-dst": ep_dst},
        syncs={"s1": sync},
    )


def _prune_active_statuses(
    config: Config,
) -> tuple[dict[str, VolumeStatus], dict[str, SyncStatus]]:
    local_ssh = localhost_ssh_status()
    vol_statuses = {
        name: vol_status(name, config, local_ssh) for name in config.volumes
    }
    sync_statuses = {
        name: SyncStatus(
            slug=name,
            config=sync,
            source_endpoint_status=src_ep_status(
                sync.source,
                vol_statuses[config.sync_endpoints[sync.source].volume],
            ),
            destination_endpoint_status=dst_ep_status(
                sync.destination,
                vol_statuses[config.sync_endpoints[sync.destination].volume],
            ),
            errors=[],
        )
        for name, sync in config.syncs.items()
    }
    return vol_statuses, sync_statuses


def _infra_error_statuses(
    config: Config,
) -> tuple[dict[str, VolumeStatus], dict[str, SyncStatus]]:
    """Destination snapshots/ missing: an infrastructure error, not inactivity."""
    vol_statuses, sync_statuses = _prune_active_statuses(config)
    return vol_statuses, {
        name: ss.model_copy(
            update={
                "destination_endpoint_status": ss.destination_endpoint_status.model_copy(
                    update={
                        "errors": [DestinationEndpointError.SNAPSHOTS_DIR_NOT_FOUND]
                    }
                ),
                "errors": [SyncError.DESTINATION_ENDPOINT_INACTIVE],
            }
        )
        for name, ss in sync_statuses.items()
    }


def _inactive_statuses(
    config: Config,
) -> tuple[dict[str, VolumeStatus], dict[str, SyncStatus]]:
    """Source sentinel missing: expected inactivity."""
    local_ssh = localhost_ssh_status()
    vol_s = {name: vol_status(name, config, local_ssh) for name in config.volumes}
    return vol_s, {
        name: SyncStatus(
            slug=name,
            config=sync,
            source_endpoint_status=src_ep_status(
                sync.source,
                vol_s[config.sync_endpoints[sync.source].volume],
                sentinel_exists=False,
            ),
            destination_endpoint_status=dst_ep_status(
                sync.destination,
                vol_s[config.sync_endpoints[sync.destination].volume],
            ),
            errors=[SyncError.SOURCE_ENDPOINT_INACTIVE],
        )
        for name, sync in config.syncs.items()
    }


def _invoke_json(*args: str) -> tuple[int, list[dict[str, object]]]:
    result = runner.invoke(app, ["snapshots", *args, "-c", "/fake.yaml", "-o", "json"])
    return result.exit_code, json.loads(result.output)


class TestPruneCommand:
    @patch("nbkp.snapshots.cli.cmd_handler.prune.list_snapshots")
    @patch("nbkp.snapshots.cli.cmd_handler.prune.btrfs_prune_snapshots")
    @patch("nbkp.commands.preflight.check_all_syncs")
    @patch("nbkp.commands.config.load_config")
    def test_successful_prune(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_prune: MagicMock,
        mock_list: MagicMock,
    ) -> None:
        config = _prune_config()
        mock_load.return_value = config
        mock_checks.return_value = preflight(*_prune_active_statuses(config))
        mock_prune.return_value = ["/dst/snapshots/old1"]
        mock_list.return_value = [_SNAP_1, _SNAP_2]

        exit_code, data = _invoke_json("prune")

        assert exit_code == 0
        assert data == [
            {
                "sync_slug": "s1",
                "deleted": ["/dst/snapshots/old1"],
                "kept": 2,
                "dry_run": False,
                "skip_reason": None,
                "error": None,
                "skipped": False,
            }
        ]
        mock_prune.assert_called_once()

    @patch("nbkp.snapshots.cli.cmd_handler.prune.list_snapshots")
    @patch("nbkp.snapshots.cli.cmd_handler.prune.btrfs_prune_snapshots")
    @patch("nbkp.commands.preflight.check_all_syncs")
    @patch("nbkp.commands.config.load_config")
    def test_dry_run(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_prune: MagicMock,
        mock_list: MagicMock,
    ) -> None:
        config = _prune_config()
        mock_load.return_value = config
        mock_checks.return_value = preflight(*_prune_active_statuses(config))
        mock_prune.return_value = ["/dst/snapshots/old1"]
        mock_list.return_value = [_SNAP_1, _SNAP_2]

        exit_code, data = _invoke_json("prune", "--dry-run")

        assert exit_code == 0
        assert data[0]["dry_run"] is True
        # Dry run: nothing deleted yet, so the would-be-deleted count is kept.
        assert data[0]["kept"] == 3
        assert mock_prune.call_args.kwargs.get("dry_run") is True

    @patch("nbkp.commands.preflight.check_all_syncs")
    @patch("nbkp.commands.config.load_config")
    def test_no_syncs_to_prune(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
    ) -> None:
        config = sample_config()  # no snapshots
        mock_load.return_value = config
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_all_active_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)

        exit_code, data = _invoke_json("prune")

        assert exit_code == 0
        assert {r["skip_reason"] for r in data} == {"no snapshots configured"}

    @patch(
        "nbkp.snapshots.cli.cmd_handler.prune.btrfs_prune_snapshots",
        side_effect=SnapshotOperationError(
            SnapshotOp.DELETE, "/dst/snapshots/old1", "Permission denied"
        ),
    )
    @patch("nbkp.commands.preflight.check_all_syncs")
    @patch("nbkp.commands.config.load_config")
    def test_snapshot_error_is_reported(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        _mock_prune: MagicMock,
    ) -> None:
        config = _prune_config()
        mock_load.return_value = config
        mock_checks.return_value = preflight(*_prune_active_statuses(config))

        exit_code, data = _invoke_json("prune")

        assert exit_code == 1
        assert data[0]["skip_reason"] is None
        assert "Permission denied" in str(data[0]["error"])

    @patch("nbkp.commands.preflight.check_all_syncs")
    @patch("nbkp.commands.config.load_config")
    def test_infra_errors_fail_by_default(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
    ) -> None:
        config = _prune_config()
        mock_load.return_value = config
        mock_checks.return_value = preflight(*_infra_error_statuses(config))

        exit_code, data = _invoke_json("prune")

        assert exit_code == 1
        assert data[0]["skip_reason"] is None
        assert str(data[0]["error"]).startswith("preflight errors:")

    @patch("nbkp.commands.preflight.check_all_syncs")
    @patch("nbkp.commands.config.load_config")
    def test_infra_errors_skipped_under_ignore_all(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
    ) -> None:
        config = _prune_config()
        mock_load.return_value = config
        mock_checks.return_value = preflight(*_infra_error_statuses(config))

        exit_code, data = _invoke_json("prune", "--strictness", "ignore-all")

        assert exit_code == 0
        assert data[0]["skip_reason"] == "preflight errors ignored"
        assert data[0]["error"] is None

    @patch("nbkp.commands.preflight.check_all_syncs")
    @patch("nbkp.commands.config.load_config")
    def test_sync_filter_limits_preflight(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
    ) -> None:
        config = _prune_config()
        mock_load.return_value = config
        mock_checks.return_value = preflight(*_inactive_statuses(config))

        exit_code, _ = _invoke_json("prune", "--sync", "s1")

        assert exit_code == 0
        assert mock_checks.call_args.kwargs.get("only_syncs") == ["s1"]


_SNAP_1 = Snapshot(
    name="2026-03-01T10:00:00.000Z",
    timestamp=datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC),
)
_SNAP_2 = Snapshot(
    name="2026-03-06T14:30:00.000Z",
    timestamp=datetime(2026, 3, 6, 14, 30, 0, tzinfo=UTC),
)


class TestShowCommand:
    @patch("nbkp.snapshots.cli.cmd_handler.show.read_latest_symlink")
    @patch("nbkp.snapshots.cli.cmd_handler.show.list_snapshots")
    @patch("nbkp.commands.preflight.check_all_syncs")
    @patch("nbkp.commands.config.load_config")
    def test_successful_show(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_list: MagicMock,
        mock_latest: MagicMock,
    ) -> None:
        config = _prune_config()
        mock_load.return_value = config
        mock_checks.return_value = preflight(*_prune_active_statuses(config))
        mock_list.return_value = [_SNAP_1, _SNAP_2]
        mock_latest.return_value = _SNAP_2

        exit_code, data = _invoke_json("show")

        assert exit_code == 0
        assert len(data) == 1
        assert data[0]["sync_slug"] == "s1"
        assert data[0]["snapshot_mode"] == "btrfs"
        assert len(data[0]["snapshots"]) == 2  # type: ignore[arg-type]
        assert data[0]["latest"]["name"] == "2026-03-06T14:30:00.000Z"  # type: ignore[index]
        assert data[0]["max_snapshots"] == 3
        assert data[0]["skip_reason"] is None
        assert data[0]["error"] is None

    def test_human_output_renders(self) -> None:
        """Smoke test of the human table (the content is asserted via JSON)."""
        with (
            patch("nbkp.commands.config.load_config") as mock_load,
            patch("nbkp.commands.preflight.check_all_syncs") as mock_checks,
            patch("nbkp.snapshots.cli.cmd_handler.show.list_snapshots") as mock_list,
            patch(
                "nbkp.snapshots.cli.cmd_handler.show.read_latest_symlink"
            ) as mock_latest,
        ):
            config = _prune_config()
            mock_load.return_value = config
            mock_checks.return_value = preflight(*_prune_active_statuses(config))
            mock_list.return_value = [_SNAP_1]
            mock_latest.return_value = _SNAP_1

            result = runner.invoke(app, ["snapshots", "show", "-c", "/fake.yaml"])

        assert result.exit_code == 0

    @patch("nbkp.commands.preflight.check_all_syncs")
    @patch("nbkp.commands.config.load_config")
    def test_no_snapshots_configured(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
    ) -> None:
        config = sample_config()  # no snapshots
        mock_load.return_value = config
        vol_s = sample_all_active_vol_statuses(config)
        sync_s = sample_all_active_sync_statuses(config, vol_s)
        mock_checks.return_value = preflight(vol_s, sync_s)

        exit_code, data = _invoke_json("show")

        assert exit_code == 0
        assert {r["skip_reason"] for r in data} == {"no snapshots configured"}

    @patch("nbkp.snapshots.cli.cmd_handler.show.read_latest_symlink")
    @patch("nbkp.snapshots.cli.cmd_handler.show.list_snapshots")
    @patch("nbkp.commands.preflight.check_all_syncs")
    @patch("nbkp.commands.config.load_config")
    def test_sync_filter(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        mock_list: MagicMock,
        mock_latest: MagicMock,
    ) -> None:
        config = _prune_config()
        mock_load.return_value = config
        mock_checks.return_value = preflight(*_prune_active_statuses(config))
        mock_list.return_value = [_SNAP_1]
        mock_latest.return_value = _SNAP_1

        exit_code, data = _invoke_json("show", "--sync", "s1")

        assert exit_code == 0
        assert [r["sync_slug"] for r in data] == ["s1"]
        assert mock_checks.call_args.kwargs.get("only_syncs") == ["s1"]

    @patch("nbkp.commands.preflight.check_all_syncs")
    @patch("nbkp.commands.config.load_config")
    def test_inactive_sync(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
    ) -> None:
        config = _prune_config()
        mock_load.return_value = config
        mock_checks.return_value = preflight(*_inactive_statuses(config))

        exit_code, data = _invoke_json("show")

        assert exit_code == 0
        assert data[0]["skip_reason"] == "inactive"
        assert data[0]["skipped"] is True

    @patch("nbkp.commands.preflight.check_all_syncs")
    @patch("nbkp.commands.config.load_config")
    def test_inactive_sync_fails_under_ignore_none(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
    ) -> None:
        config = _prune_config()
        mock_load.return_value = config
        mock_checks.return_value = preflight(*_inactive_statuses(config))

        exit_code, data = _invoke_json("show", "-S", "ignore-none")

        assert exit_code == 1
        assert str(data[0]["error"]).startswith("inactive:")

    @patch("nbkp.commands.preflight.check_all_syncs")
    @patch("nbkp.commands.config.load_config")
    def test_infra_errors_fail(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
    ) -> None:
        """A sync broken by infrastructure errors is not silently skipped."""
        config = _prune_config()
        mock_load.return_value = config
        mock_checks.return_value = preflight(*_infra_error_statuses(config))

        exit_code, data = _invoke_json("show")

        assert exit_code == 1
        assert data[0]["skip_reason"] is None
        assert data[0]["error"] == "preflight errors: destination endpoint inactive"

    @patch(
        "nbkp.snapshots.cli.cmd_handler.show.list_snapshots",
        side_effect=SnapshotOperationError(
            SnapshotOp.LIST, "/dst/snapshots", "connection failed"
        ),
    )
    @patch("nbkp.commands.preflight.check_all_syncs")
    @patch("nbkp.commands.config.load_config")
    def test_snapshot_error(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
        _mock_list: MagicMock,
    ) -> None:
        config = _prune_config()
        mock_load.return_value = config
        mock_checks.return_value = preflight(*_prune_active_statuses(config))

        exit_code, data = _invoke_json("show")

        assert exit_code == 1
        assert "connection failed" in str(data[0]["error"])

    @patch("nbkp.commands.preflight.check_all_syncs")
    @patch("nbkp.commands.config.load_config")
    def test_strictness_forwarded_to_managed_mount(
        self,
        mock_load: MagicMock,
        mock_checks: MagicMock,
    ) -> None:
        config = _prune_config()
        mock_load.return_value = config
        mock_checks.return_value = preflight(*_inactive_statuses(config))

        with patch(
            "nbkp.snapshots.cli.show_cmd.managed_mount", wraps=managed_mount
        ) as spy:
            _invoke_json("show", "-S", "ignore-all")

        assert spy.call_args.kwargs.get("strictness") is Strictness.IGNORE_ALL
