import os
from contextlib import ExitStack
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from unittest.mock import MagicMock, patch

import paramiko
import pytest

from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
    Volume,
)
from nbkp.preflight.checks import (
    check_all_syncs,
    check_step_count,
)
from nbkp.preflight.probes import (
    check_endpoint_sentinel,
    check_rsync_version,
    parse_rsync_version,
)
from nbkp.preflight.snapshot_checks import (
    check_btrfs_mount_option,
    check_btrfs_subvolume,
    is_btrfs_filesystem,
    probe_filesystem_type,
    supports_hard_links,
)
from nbkp.preflight.status.endpoint import (
    DestinationEndpointDiagnostics,
    DestinationEndpointError,
    DestinationEndpointStatus,
    SourceEndpointDiagnostics,
    SourceEndpointError,
    SourceEndpointStatus,
)
from nbkp.preflight.status.ssh import (
    HostToolCapabilities,
    SshEndpointDiagnostics,
    SshEndpointError,
    SshEndpointStatus,
)
from nbkp.preflight.status.sync import SyncError, SyncStatus
from nbkp.preflight.status.volume import (
    VolumeCapabilities,
    VolumeDiagnostics,
    VolumeError,
    VolumeStatus,
)
from nbkp.preflight.testkit import check_sync, check_volume
from nbkp.preflight.volume_checks import check_volume_capabilities, observe_ssh_endpoint
from nbkp.remote.endpoints import EndpointFilter, ResolvedEndpoint, ResolvedEndpoints
from nbkp.remote.resolution import resolve_all_endpoints, resolve_proxy_chain
from nbkp.snapshots.common import create_snapshot_timestamp
from tests.fakes import FakeCommandRunner, completed
from tests.preflight.fake_hosts import FAILS


@pytest.fixture(autouse=True)
def _no_real_host_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the check pipeline from spawning real ``rsync`` / ``test -w``.

    Unit tests must not depend on the developer's rsync (project rule): the
    version probe reports a good rsync, and writability is answered by
    ``os.access`` — the same question ``test -w`` asks, without a process.
    Tests exercising these probes patch them (or ``subprocess.run``) directly.
    """
    monkeypatch.setattr(
        "nbkp.preflight.volume_checks.check_rsync_version", lambda *_, **__: True
    )
    monkeypatch.setattr(
        "nbkp.preflight.endpoint_checks.check_directory_writable",
        lambda _vol, path, _re, **_kw: os.access(path, os.W_OK),
    )


_STUB_HOST_TOOLS = HostToolCapabilities(
    has_rsync=True,
    rsync_version_ok=True,
    has_btrfs=False,
    has_stat=True,
    has_findmnt=True,
)

_NO_RSYNC_HOST_TOOLS = HostToolCapabilities(
    has_rsync=False,
    rsync_version_ok=False,
    has_btrfs=False,
    has_stat=False,
    has_findmnt=False,
)

_BTRFS_FULL_HOST_TOOLS = HostToolCapabilities(
    has_rsync=True,
    rsync_version_ok=True,
    has_btrfs=True,
    has_stat=True,
    has_findmnt=True,
)

_BTRFS_NO_BTRFS_CMD_HOST_TOOLS = HostToolCapabilities(
    has_rsync=True,
    rsync_version_ok=True,
    has_btrfs=False,
    has_stat=True,
    has_findmnt=True,
)

_BTRFS_NO_STAT_HOST_TOOLS = HostToolCapabilities(
    has_rsync=True,
    rsync_version_ok=True,
    has_btrfs=True,
    has_stat=False,
    has_findmnt=True,
)

_BTRFS_NO_FINDMNT_HOST_TOOLS = HostToolCapabilities(
    has_rsync=True,
    rsync_version_ok=True,
    has_btrfs=True,
    has_stat=True,
    has_findmnt=False,
)

_BTRFS_NO_STAT_NO_FINDMNT_HOST_TOOLS = HostToolCapabilities(
    has_rsync=True,
    rsync_version_ok=True,
    has_btrfs=True,
    has_stat=False,
    has_findmnt=False,
)

_BTRFS_NOT_BTRFS_FS_HOST_TOOLS = HostToolCapabilities(
    has_rsync=True,
    rsync_version_ok=True,
    has_btrfs=True,
    has_stat=True,
    has_findmnt=True,
)

_HL_NO_STAT_HOST_TOOLS = HostToolCapabilities(
    has_rsync=True,
    rsync_version_ok=True,
    has_btrfs=False,
    has_stat=False,
    has_findmnt=False,
)

_HL_NO_HARDLINK_HOST_TOOLS = HostToolCapabilities(
    has_rsync=True,
    rsync_version_ok=True,
    has_btrfs=False,
    has_stat=True,
    has_findmnt=False,
)

_REACHABLE_SSH_DIAG = SshEndpointDiagnostics(ssh_reachable=True)
"""Shared default bastion diagnostics; safe to share because the model is frozen."""


def _make_localhost_ssh(
    host_tools: HostToolCapabilities = _STUB_HOST_TOOLS,
) -> SshEndpointStatus:
    return SshEndpointStatus(
        slug="localhost",
        diagnostics=SshEndpointDiagnostics(host_tools=host_tools),
        errors=[],
    )


_ACTIVE_LOCALHOST_SSH = _make_localhost_ssh()


_STUB_CAPS = VolumeCapabilities(
    sentinel_exists=True,
    is_btrfs_filesystem=False,
    hardlink_supported=True,
    btrfs_user_subvol_rm=False,
)

_NO_RSYNC_CAPS = VolumeCapabilities(
    sentinel_exists=True,
    is_btrfs_filesystem=False,
    hardlink_supported=True,
    btrfs_user_subvol_rm=False,
)

_BTRFS_FULL_CAPS = VolumeCapabilities(
    sentinel_exists=True,
    is_btrfs_filesystem=True,
    hardlink_supported=True,
    btrfs_user_subvol_rm=True,
)

_BTRFS_NO_SUBVOL_RM_CAPS = VolumeCapabilities(
    sentinel_exists=True,
    is_btrfs_filesystem=True,
    hardlink_supported=True,
    btrfs_user_subvol_rm=False,
)

_BTRFS_NO_BTRFS_CMD_CAPS = VolumeCapabilities(
    sentinel_exists=True,
    is_btrfs_filesystem=False,
    hardlink_supported=True,
    btrfs_user_subvol_rm=False,
)

_BTRFS_NO_STAT_CAPS = VolumeCapabilities(
    sentinel_exists=True,
    is_btrfs_filesystem=False,
    hardlink_supported=True,
    btrfs_user_subvol_rm=False,
)

_BTRFS_NO_FINDMNT_CAPS = VolumeCapabilities(
    sentinel_exists=True,
    is_btrfs_filesystem=True,
    hardlink_supported=True,
    btrfs_user_subvol_rm=False,
)

_BTRFS_NO_STAT_NO_FINDMNT_CAPS = VolumeCapabilities(
    sentinel_exists=True,
    is_btrfs_filesystem=False,
    hardlink_supported=True,
    btrfs_user_subvol_rm=False,
)

_BTRFS_NOT_BTRFS_FS_CAPS = VolumeCapabilities(
    sentinel_exists=True,
    is_btrfs_filesystem=False,
    hardlink_supported=True,
    btrfs_user_subvol_rm=False,
)

_HL_NO_STAT_CAPS = VolumeCapabilities(
    sentinel_exists=True,
    is_btrfs_filesystem=False,
    hardlink_supported=True,
    btrfs_user_subvol_rm=False,
)

_HL_NO_HARDLINK_CAPS = VolumeCapabilities(
    sentinel_exists=True,
    is_btrfs_filesystem=False,
    hardlink_supported=False,
    btrfs_user_subvol_rm=False,
)


class TestVolumeStatus:
    def test_construction_active(self) -> None:
        vol = LocalVolume(slug="data", path="/mnt/data")
        vs = VolumeStatus(
            slug="data",
            config=vol,
            ssh_endpoint_status=_ACTIVE_LOCALHOST_SSH,
            diagnostics=VolumeDiagnostics(),
            errors=[],
        )
        assert vs.active is True

    def test_construction_inactive(self) -> None:
        vol = LocalVolume(slug="data", path="/mnt/data")
        sentinel_missing_caps = VolumeCapabilities(
            sentinel_exists=False,
            is_btrfs_filesystem=False,
            hardlink_supported=True,
            btrfs_user_subvol_rm=False,
        )
        vs = VolumeStatus(
            slug="data",
            config=vol,
            ssh_endpoint_status=_ACTIVE_LOCALHOST_SSH,
            diagnostics=VolumeDiagnostics(capabilities=sentinel_missing_caps),
            errors=[VolumeError.SENTINEL_NOT_FOUND],
        )
        assert vs.active is False


class TestSyncStatus:
    def test_construction_active(self) -> None:
        vol = LocalVolume(slug="data", path="/mnt/data")
        vs = VolumeStatus(
            slug="data",
            config=vol,
            ssh_endpoint_status=_ACTIVE_LOCALHOST_SSH,
            diagnostics=VolumeDiagnostics(),
            errors=[],
        )
        src_ep_status = SourceEndpointStatus(
            endpoint_slug="ep-src",
            volume_status=vs,
            diagnostics=None,
            errors=[],
        )
        dst_ep_status = DestinationEndpointStatus(
            endpoint_slug="ep-dst",
            volume_status=vs,
            diagnostics=None,
            errors=[],
        )
        sc = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
        )
        ss = SyncStatus(
            slug="s1",
            config=sc,
            source_endpoint_status=src_ep_status,
            destination_endpoint_status=dst_ep_status,
            errors=[],
        )
        assert ss.active is True

    def test_construction_inactive(self) -> None:
        vol = LocalVolume(slug="data", path="/mnt/data")
        vs = VolumeStatus(
            slug="data",
            config=vol,
            ssh_endpoint_status=_ACTIVE_LOCALHOST_SSH,
            diagnostics=VolumeDiagnostics(),
            errors=[],
        )
        src_ep_status = SourceEndpointStatus(
            endpoint_slug="ep-src",
            volume_status=vs,
            diagnostics=None,
            errors=[],
        )
        dst_ep_status = DestinationEndpointStatus(
            endpoint_slug="ep-dst",
            volume_status=vs,
            diagnostics=None,
            errors=[],
        )
        sc = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
        )
        ss = SyncStatus(
            slug="s1",
            config=sc,
            source_endpoint_status=src_ep_status,
            destination_endpoint_status=dst_ep_status,
            errors=[SyncError.DISABLED],
        )
        assert ss.active is False


def _remote_config(
    vol_name: str = "nas",
    server_name: str = "nas-server",
    host: str = "nas.local",
    path: str = "/backup",
) -> tuple[RemoteVolume, Config]:
    server = SshEndpoint(slug=server_name, host=host)
    vol = RemoteVolume(
        slug=vol_name,
        ssh_endpoint=server_name,
        path=path,
    )
    config = Config(
        ssh_endpoints={server_name: server},
        volumes={vol_name: vol},
    )
    return vol, config


def _make_resolved(config: Config) -> ResolvedEndpoints:
    result: ResolvedEndpoints = {}
    for slug, vol in config.volumes.items():
        if isinstance(vol, RemoteVolume):
            server = config.ssh_endpoints[vol.ssh_endpoint]
            proxy_chain = resolve_proxy_chain(config, server)
            result[slug] = ResolvedEndpoint(server=server, proxy_chain=proxy_chain)
    return result


class TestCheckLocalVolume:
    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    def test_active(self, mock_caps: MagicMock, tmp_path: Path) -> None:
        vol = LocalVolume(slug="data", path=str(tmp_path))
        (tmp_path / ".nbkp-vol").touch()
        status = check_volume(vol)
        assert status.active is True
        assert status.errors == []
        assert status.diagnostics.capabilities is _STUB_CAPS
        mock_caps.assert_called_once()

    def test_inactive(self, tmp_path: Path) -> None:
        vol = LocalVolume(slug="data", path=str(tmp_path))
        status = check_volume(vol)
        assert status.active is False
        assert status.errors == [VolumeError.SENTINEL_NOT_FOUND]

    def test_sentinel_directory_is_not_a_sentinel(self, tmp_path: Path) -> None:
        """Like the remote ``test -f``: only a regular file counts."""
        (tmp_path / ".nbkp-vol").mkdir()
        status = check_volume(LocalVolume(slug="data", path=str(tmp_path)))
        assert status.errors == [VolumeError.SENTINEL_NOT_FOUND]

    def test_endpoint_sentinel_directory_is_not_a_sentinel(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "photos" / ".nbkp-src").mkdir(parents=True)
        vol = LocalVolume(slug="data", path=str(tmp_path))
        assert check_endpoint_sentinel(vol, "photos", ".nbkp-src", {}) is False
        (tmp_path / "photos" / ".nbkp-src").rmdir()
        (tmp_path / "photos" / ".nbkp-src").touch()
        assert check_endpoint_sentinel(vol, "photos", ".nbkp-src", {}) is True


class TestCheckRemoteVolume:
    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    @patch("nbkp.preflight.checks.observe_ssh_endpoint")
    @patch("nbkp.remote.dispatch.run_remote_command")
    def test_active(
        self, mock_run: MagicMock, mock_ssh_ep: MagicMock, mock_caps: MagicMock
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        mock_ssh_ep.return_value = SshEndpointDiagnostics(
            ssh_reachable=True,
            host_tools=_STUB_HOST_TOOLS,
        )
        vol, config = _remote_config()
        resolved = _make_resolved(config)
        status = check_volume(vol, resolved)
        assert status.active is True
        assert status.errors == []
        assert status.diagnostics is not None
        assert status.diagnostics.capabilities is _STUB_CAPS

    @patch("nbkp.preflight.checks.observe_ssh_endpoint")
    @patch("nbkp.remote.dispatch.run_remote_command")
    def test_sentinel_missing(
        self, mock_run: MagicMock, mock_ssh_ep: MagicMock
    ) -> None:
        """SSH succeeds but sentinel file is absent."""
        mock_run.return_value = MagicMock(returncode=1)
        mock_ssh_ep.return_value = SshEndpointDiagnostics(
            ssh_reachable=True,
            host_tools=_STUB_HOST_TOOLS,
        )
        vol, config = _remote_config()
        resolved = _make_resolved(config)
        status = check_volume(vol, resolved)
        assert status.active is False
        assert status.errors == [VolumeError.SENTINEL_NOT_FOUND]
        assert status.ssh_endpoint_status.diagnostics.ssh_reachable is True
        assert status.diagnostics is not None
        assert status.diagnostics.capabilities is not None
        assert status.diagnostics.capabilities.sentinel_exists is False

    @patch("nbkp.preflight.checks.observe_ssh_endpoint")
    def test_ssh_unreachable(self, mock_ssh_ep: MagicMock) -> None:
        """SSH connection fails entirely."""
        mock_ssh_ep.return_value = SshEndpointDiagnostics(ssh_reachable=False)
        vol, config = _remote_config()
        resolved = _make_resolved(config)
        status = check_volume(vol, resolved)
        assert status.active is False
        assert SshEndpointError.UNREACHABLE in status.ssh_endpoint_status.errors
        assert status.ssh_endpoint_status.diagnostics.ssh_reachable is False
        assert status.diagnostics is None


class TestCheckRemoteVolumeLocationExcluded:
    @patch("nbkp.preflight.checks.observe_ssh_endpoint")
    def test_excluded_volume_no_ssh(self, mock_ssh_ep: MagicMock) -> None:
        """Volume excluded by location is marked without SSH."""
        mock_ssh_ep.return_value = SshEndpointDiagnostics(location_excluded=True)
        vol, _config = _remote_config()
        # Empty resolved_endpoints means volume was excluded
        status = check_volume(vol, {})
        assert status.active is False
        assert SshEndpointError.LOCATION_EXCLUDED in status.ssh_endpoint_status.errors


class TestProbeFilesystemTypeLocal:
    def test_btrfs(self) -> None:
        run = FakeCommandRunner().on("stat", returns=completed(stdout="btrfs\n"))
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert probe_filesystem_type(vol, {}, run=run) == "btrfs"
        assert run.commands == [["stat", "-f", "-c", "%T", "/mnt/data"]]
        assert run.calls[0].volume == vol

    def test_stat_failure(self) -> None:
        run = FakeCommandRunner().on("stat", returns=FAILS)
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert probe_filesystem_type(vol, {}, run=run) is None


class TestProbeFilesystemTypeRemote:
    def test_btrfs(self) -> None:
        run = FakeCommandRunner().on("stat", returns=completed(stdout="btrfs\n"))
        vol, config = _remote_config()
        resolved = _make_resolved(config)
        assert probe_filesystem_type(vol, resolved, run=run) == "btrfs"
        assert run.commands == [["stat", "-f", "-c", "%T", "/backup"]]
        assert run.calls[0].endpoint == resolved["nas"]


class TestFilesystemTypeInterpretation:
    def test_btrfs(self) -> None:
        assert is_btrfs_filesystem("btrfs") is True
        assert supports_hard_links("btrfs") is True

    def test_not_btrfs(self) -> None:
        assert is_btrfs_filesystem("ext2/ext3") is False
        assert supports_hard_links("ext2/ext3") is True

    def test_unknown(self) -> None:
        # Unknown, not "not btrfs": a failed probe must not trigger VOL_NOT_BTRFS.
        assert is_btrfs_filesystem(None) is None
        # Cannot determine, so hard links are assumed to work.
        assert supports_hard_links(None) is True

    @pytest.mark.parametrize("fs_type", ["vfat", "msdos", "exfat"])
    def test_no_hard_links(self, fs_type: str) -> None:
        assert supports_hard_links(fs_type) is False


class TestCheckVolumeCapabilitiesFilesystemProbe:
    def test_stat_runs_once_per_volume(self) -> None:
        run = FakeCommandRunner().on("stat", returns=completed(stdout="msdos\n"))
        vol, config = _remote_config()
        resolved = _make_resolved(config)
        tools = HostToolCapabilities(
            has_rsync=True,
            rsync_version_ok=True,
            has_btrfs=False,
            has_stat=True,
            has_findmnt=False,
        )
        caps = check_volume_capabilities(
            vol, tools, resolved_endpoints=resolved, run=run
        )
        assert caps.is_btrfs_filesystem is False
        assert caps.hardlink_supported is False
        assert run.commands == [["stat", "-f", "-c", "%T", "/backup"]]


class TestCheckBtrfsSubvolumeLocal:
    def test_is_subvolume(self) -> None:
        run = FakeCommandRunner().on("stat", returns=completed(stdout="256\n"))
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert check_btrfs_subvolume(vol, None, {}, run=run) is True
        assert run.commands == [["stat", "-c", "%i", "/mnt/data"]]

    def test_is_subvolume_with_subdir(self) -> None:
        run = FakeCommandRunner().on("stat", returns=completed(stdout="256\n"))
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert check_btrfs_subvolume(vol, "backup", {}, run=run) is True
        assert run.commands == [["stat", "-c", "%i", "/mnt/data/backup"]]

    def test_not_subvolume(self) -> None:
        run = FakeCommandRunner().on("stat", returns=completed(stdout="1234\n"))
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert check_btrfs_subvolume(vol, None, {}, run=run) is False

    def test_stat_failure(self) -> None:
        run = FakeCommandRunner().on("stat", returns=FAILS)
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert check_btrfs_subvolume(vol, None, {}, run=run) is False


class TestCheckBtrfsSubvolumeRemote:
    def test_is_subvolume(self) -> None:
        run = FakeCommandRunner().on("stat", returns=completed(stdout="256\n"))
        vol, config = _remote_config()
        resolved = _make_resolved(config)
        assert check_btrfs_subvolume(vol, None, resolved, run=run) is True
        assert run.commands == [["stat", "-c", "%i", "/backup"]]
        assert run.calls[0].endpoint == resolved["nas"]

    def test_is_subvolume_with_subdir(self) -> None:
        run = FakeCommandRunner().on("stat", returns=completed(stdout="256\n"))
        vol, config = _remote_config()
        resolved = _make_resolved(config)
        assert check_btrfs_subvolume(vol, "data", resolved, run=run) is True
        assert run.commands == [["stat", "-c", "%i", "/backup/data"]]

    def test_not_subvolume(self) -> None:
        run = FakeCommandRunner().on("stat", returns=completed(stdout="1234\n"))
        vol, config = _remote_config()
        resolved = _make_resolved(config)
        assert check_btrfs_subvolume(vol, None, resolved, run=run) is False


class TestCheckBtrfsMountOptionLocal:
    def test_option_present(self) -> None:
        run = FakeCommandRunner().on(
            "findmnt", returns=completed(stdout="rw,relatime,user_subvol_rm_allowed\n")
        )
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert (
            check_btrfs_mount_option(vol, "user_subvol_rm_allowed", {}, run=run) is True
        )
        assert run.commands == [["findmnt", "-T", "/mnt/data", "-n", "-o", "OPTIONS"]]

    def test_option_missing(self) -> None:
        run = FakeCommandRunner().on(
            "findmnt", returns=completed(stdout="rw,relatime\n")
        )
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert (
            check_btrfs_mount_option(vol, "user_subvol_rm_allowed", {}, run=run)
            is False
        )

    def test_findmnt_failure(self) -> None:
        run = FakeCommandRunner().on("findmnt", returns=FAILS)
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert (
            check_btrfs_mount_option(vol, "user_subvol_rm_allowed", {}, run=run)
            is False
        )


class TestCheckBtrfsMountOptionRemote:
    def test_option_present(self) -> None:
        run = FakeCommandRunner().on(
            "findmnt", returns=completed(stdout="rw,relatime,user_subvol_rm_allowed\n")
        )
        vol, config = _remote_config()
        resolved = _make_resolved(config)
        assert (
            check_btrfs_mount_option(vol, "user_subvol_rm_allowed", resolved, run=run)
            is True
        )
        assert run.commands == [["findmnt", "-T", "/backup", "-n", "-o", "OPTIONS"]]
        assert run.calls[0].endpoint == resolved["nas"]

    def test_option_missing(self) -> None:
        run = FakeCommandRunner().on(
            "findmnt", returns=completed(stdout="rw,relatime\n")
        )
        vol, config = _remote_config()
        resolved = _make_resolved(config)
        assert (
            check_btrfs_mount_option(vol, "user_subvol_rm_allowed", resolved, run=run)
            is False
        )


def _ssh_diag(
    host_tools: HostToolCapabilities = _STUB_HOST_TOOLS,
    ssh_reachable: bool | None = None,
    location_excluded: bool = False,
) -> SshEndpointDiagnostics:
    return SshEndpointDiagnostics(
        ssh_reachable=ssh_reachable,
        location_excluded=location_excluded,
        host_tools=host_tools,
    )


class TestCheckSync:
    def _make_config(self, tmp_src: Path, tmp_dst: Path) -> tuple[Config, SyncConfig]:
        src_vol = LocalVolume(slug="src", path=str(tmp_src))
        dst_vol = LocalVolume(slug="dst", path=str(tmp_dst))
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            volumes={"src": src_vol, "dst": dst_vol},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src", subdir="data"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst", subdir="backup"),
            },
            syncs={"s1": sync},
        )
        return config, sync

    @patch(
        "nbkp.preflight.checks.observe_ssh_endpoint",
        return_value=_ssh_diag(),
    )
    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    def test_active_sync(
        self,
        _mock_caps: MagicMock,
        _mock_ssh: MagicMock,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()

        (src / ".nbkp-vol").touch()
        (dst / ".nbkp-vol").touch()
        (src / "data").mkdir()
        (src / "data" / ".nbkp-src").touch()
        (dst / "backup").mkdir()
        (dst / "backup" / ".nbkp-dst").touch()

        config, sync = self._make_config(src, dst)

        status = check_sync(sync, config)
        assert status.active is True
        assert status.errors == []

    @patch(
        "nbkp.preflight.checks.observe_ssh_endpoint",
        return_value=_ssh_diag(),
    )
    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    def test_disabled_sync(
        self, _mock_caps: MagicMock, _mock_ssh: MagicMock, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()

        config, _ = self._make_config(src, dst)
        sync = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
            enabled=False,
        )
        config = Config(
            volumes=config.volumes,
            sync_endpoints=config.sync_endpoints,
            syncs={"s1": sync},
        )

        status = check_sync(sync, config)
        assert status.active is False
        assert status.errors == [SyncError.DISABLED]

    @patch(
        "nbkp.preflight.checks.observe_ssh_endpoint",
        return_value=_ssh_diag(),
    )
    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    def test_source_unavailable(
        self, _mock_caps: MagicMock, _mock_ssh: MagicMock, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        # No .nbkp-vol on src, so it becomes inactive
        (dst / ".nbkp-vol").touch()
        (dst / "backup").mkdir()
        (dst / "backup" / ".nbkp-dst").touch()

        config, sync = self._make_config(src, dst)

        status = check_sync(sync, config)
        assert status.active is False
        src_vol_errors = status.source_endpoint_status.volume_status.errors
        assert VolumeError.SENTINEL_NOT_FOUND in src_vol_errors

    @patch(
        "nbkp.preflight.checks.observe_ssh_endpoint",
        return_value=_ssh_diag(),
    )
    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    def test_missing_src_sentinel(
        self, _mock_caps: MagicMock, _mock_ssh: MagicMock, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        (src / ".nbkp-vol").touch()
        (dst / ".nbkp-vol").touch()
        (src / "data").mkdir()
        (dst / "backup").mkdir()
        (dst / "backup" / ".nbkp-dst").touch()

        config, sync = self._make_config(src, dst)

        status = check_sync(sync, config)
        assert status.active is False
        assert (
            SourceEndpointError.SENTINEL_NOT_FOUND
            in status.source_endpoint_status.errors
        )

    def _setup_active_sentinels(self, src: Path, dst: Path) -> None:
        (src / ".nbkp-vol").touch()
        (dst / ".nbkp-vol").touch()
        (src / "data").mkdir(exist_ok=True)
        (src / "data" / ".nbkp-src").touch()
        (dst / "backup").mkdir(exist_ok=True)
        (dst / "backup" / ".nbkp-dst").touch()

    @patch(
        "nbkp.preflight.checks.observe_ssh_endpoint",
        return_value=_ssh_diag(host_tools=_NO_RSYNC_HOST_TOOLS),
    )
    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_NO_RSYNC_CAPS,
    )
    def test_rsync_not_found_on_source(
        self, _mock_caps: MagicMock, _mock_ssh: MagicMock, tmp_path: Path
    ) -> None:
        """Rsync missing on a local volume: reported at SSH endpoint level,
        but local volumes are still probed (no SSH_ENDPOINT_INACTIVE cascade)."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_active_sentinels(src, dst)

        config, sync = self._make_config(src, dst)

        status = check_sync(sync, config)
        # rsync errors are at the SSH endpoint level (shared by both volumes)
        ssh_errors = (
            status.source_endpoint_status.volume_status.ssh_endpoint_status.errors
        )
        assert SshEndpointError.RSYNC_NOT_FOUND in ssh_errors
        # Local volumes don't cascade SSH_ENDPOINT_INACTIVE — volume is active
        assert status.source_endpoint_status.volume_status.active is True
        # ...but the sync is inactive, and not expected-inactive: a missing
        # localhost rsync must fail preflight.
        assert SyncError.ENDPOINT_HOST_ERRORS in status.errors
        assert status.is_expected_inactive() is False

    @patch(
        "nbkp.preflight.checks.observe_ssh_endpoint",
        return_value=_ssh_diag(host_tools=_BTRFS_NO_BTRFS_CMD_HOST_TOOLS),
    )
    @patch("nbkp.preflight.volume_checks.check_volume_capabilities")
    def test_rsync_found_btrfs_not_found_on_destination(
        self, mock_caps: MagicMock, _mock_ssh: MagicMock, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_active_sentinels(src, dst)

        src_vol = LocalVolume(slug="src", path=str(src))
        dst_vol = LocalVolume(slug="dst", path=str(dst))
        sync = SyncConfig(
            slug="s1",
            source="ep-btrfs-src",
            destination="ep-btrfs-dst",
        )
        config = Config(
            volumes={"src": src_vol, "dst": dst_vol},
            sync_endpoints={
                "ep-btrfs-src": SyncEndpoint(
                    slug="ep-btrfs-src",
                    volume="src",
                    subdir="data",
                ),
                "ep-btrfs-dst": SyncEndpoint(
                    slug="ep-btrfs-dst",
                    volume="dst",
                    subdir="backup",
                    btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
                ),
            },
            syncs={"s1": sync},
        )

        def caps_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            return _BTRFS_NO_BTRFS_CMD_CAPS if volume.slug == "dst" else _STUB_CAPS

        mock_caps.side_effect = caps_side_effect

        status = check_sync(sync, config)
        assert status.active is False
        ssh_errors = (
            status.destination_endpoint_status.volume_status.ssh_endpoint_status.errors
        )
        assert SshEndpointError.BTRFS_NOT_FOUND in ssh_errors

    @patch(
        "nbkp.preflight.checks.observe_ssh_endpoint",
        return_value=_ssh_diag(host_tools=_BTRFS_NO_BTRFS_CMD_HOST_TOOLS),
    )
    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    def test_disabled_sync_does_not_add_tool_needs(
        self, _mock_caps: MagicMock, _mock_ssh: MagicMock, tmp_path: Path
    ) -> None:
        """A disabled btrfs sync must not make the host's missing btrfs fatal
        for the enabled syncs sharing it."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_active_sentinels(src, dst)
        config = Config(
            volumes={
                "src": LocalVolume(slug="src", path=str(src)),
                "dst": LocalVolume(slug="dst", path=str(dst)),
            },
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src", subdir="data"),
                "ep-plain": SyncEndpoint(
                    slug="ep-plain", volume="dst", subdir="backup"
                ),
                "ep-old-src": SyncEndpoint(
                    slug="ep-old-src", volume="src", subdir="old"
                ),
                "ep-btrfs": SyncEndpoint(
                    slug="ep-btrfs",
                    volume="dst",
                    subdir="btrfs",
                    btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
                ),
            },
            syncs={
                "plain": SyncConfig(
                    slug="plain", source="ep-src", destination="ep-plain"
                ),
                "old-btrfs": SyncConfig(
                    slug="old-btrfs",
                    source="ep-old-src",
                    destination="ep-btrfs",
                    enabled=False,
                ),
            },
        )

        result = check_all_syncs(config)
        assert result.ssh_endpoint_statuses["localhost"].errors == []
        assert result.sync_statuses["plain"].active is True

    @patch(
        "nbkp.preflight.checks.observe_ssh_endpoint",
        return_value=_ssh_diag(host_tools=_BTRFS_NO_STAT_HOST_TOOLS),
    )
    @patch("nbkp.preflight.volume_checks.check_volume_capabilities")
    def test_stat_not_found_on_destination(
        self, mock_caps: MagicMock, _mock_ssh: MagicMock, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_active_sentinels(src, dst)

        src_vol = LocalVolume(slug="src", path=str(src))
        dst_vol = LocalVolume(slug="dst", path=str(dst))
        sync = SyncConfig(
            slug="s1",
            source="ep-stat-src",
            destination="ep-stat-dst",
        )
        config = Config(
            volumes={"src": src_vol, "dst": dst_vol},
            sync_endpoints={
                "ep-stat-src": SyncEndpoint(
                    slug="ep-stat-src",
                    volume="src",
                    subdir="data",
                ),
                "ep-stat-dst": SyncEndpoint(
                    slug="ep-stat-dst",
                    volume="dst",
                    subdir="backup",
                    btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
                ),
            },
            syncs={"s1": sync},
        )

        def caps_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            return _BTRFS_NO_STAT_CAPS if volume.slug == "dst" else _STUB_CAPS

        mock_caps.side_effect = caps_side_effect

        status = check_sync(sync, config)
        assert status.active is False
        ssh_errors = (
            status.destination_endpoint_status.volume_status.ssh_endpoint_status.errors
        )
        assert SshEndpointError.STAT_NOT_FOUND in ssh_errors
        dst_ep_errors = status.destination_endpoint_status.errors
        assert DestinationEndpointError.VOL_NOT_BTRFS not in dst_ep_errors
        assert DestinationEndpointError.STAGING_NOT_BTRFS_SUBVOLUME not in dst_ep_errors

    @patch(
        "nbkp.preflight.checks.observe_ssh_endpoint",
        return_value=_ssh_diag(host_tools=_BTRFS_NO_FINDMNT_HOST_TOOLS),
    )
    @patch("nbkp.preflight.volume_checks.check_volume_capabilities")
    @patch("nbkp.remote.dispatch.subprocess.run")
    def test_findmnt_not_found_on_destination(
        self,
        mock_subprocess: MagicMock,
        mock_caps: MagicMock,
        _mock_ssh: MagicMock,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_active_sentinels(src, dst)
        (dst / "backup" / "staging").mkdir()
        (dst / "backup" / "snapshots").mkdir()

        src_vol = LocalVolume(slug="src", path=str(src))
        dst_vol = LocalVolume(slug="dst", path=str(dst))
        sync = SyncConfig(
            slug="s1",
            source="ep-findmnt-src",
            destination="ep-findmnt-dst",
        )
        config = Config(
            volumes={"src": src_vol, "dst": dst_vol},
            sync_endpoints={
                "ep-findmnt-src": SyncEndpoint(
                    slug="ep-findmnt-src",
                    volume="src",
                    subdir="data",
                ),
                "ep-findmnt-dst": SyncEndpoint(
                    slug="ep-findmnt-dst",
                    volume="dst",
                    subdir="backup",
                    btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
                ),
            },
            syncs={"s1": sync},
        )

        def caps_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            return _BTRFS_NO_FINDMNT_CAPS if volume.slug == "dst" else _STUB_CAPS

        mock_caps.side_effect = caps_side_effect

        def subprocess_side_effect(cmd: list[str], **kwargs: object) -> MagicMock:
            if cmd[:3] == ["stat", "-c", "%i"]:
                return MagicMock(returncode=0, stdout="256\n")
            return MagicMock(returncode=0)

        mock_subprocess.side_effect = subprocess_side_effect

        status = check_sync(sync, config)
        assert status.active is False
        ssh_errors = (
            status.destination_endpoint_status.volume_status.ssh_endpoint_status.errors
        )
        assert SshEndpointError.FINDMNT_NOT_FOUND in ssh_errors
        dst_ep_errors = status.destination_endpoint_status.errors
        assert (
            DestinationEndpointError.VOL_NOT_MOUNTED_USER_SUBVOL_RM not in dst_ep_errors
        )

    @patch(
        "nbkp.preflight.checks.observe_ssh_endpoint",
        return_value=_ssh_diag(host_tools=_BTRFS_NO_STAT_NO_FINDMNT_HOST_TOOLS),
    )
    @patch("nbkp.preflight.volume_checks.check_volume_capabilities")
    def test_stat_and_findmnt_both_missing(
        self, mock_caps: MagicMock, _mock_ssh: MagicMock, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_active_sentinels(src, dst)

        src_vol = LocalVolume(slug="src", path=str(src))
        dst_vol = LocalVolume(slug="dst", path=str(dst))
        sync = SyncConfig(
            slug="s1",
            source="ep-statfm-src",
            destination="ep-statfm-dst",
        )
        config = Config(
            volumes={"src": src_vol, "dst": dst_vol},
            sync_endpoints={
                "ep-statfm-src": SyncEndpoint(
                    slug="ep-statfm-src",
                    volume="src",
                    subdir="data",
                ),
                "ep-statfm-dst": SyncEndpoint(
                    slug="ep-statfm-dst",
                    volume="dst",
                    subdir="backup",
                    btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
                ),
            },
            syncs={"s1": sync},
        )

        def caps_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            return (
                _BTRFS_NO_STAT_NO_FINDMNT_CAPS if volume.slug == "dst" else _STUB_CAPS
            )

        mock_caps.side_effect = caps_side_effect

        status = check_sync(sync, config)
        assert status.active is False
        ssh_errors = (
            status.destination_endpoint_status.volume_status.ssh_endpoint_status.errors
        )
        assert SshEndpointError.STAT_NOT_FOUND in ssh_errors
        assert SshEndpointError.FINDMNT_NOT_FOUND in ssh_errors

    @patch(
        "nbkp.preflight.checks.observe_ssh_endpoint",
        return_value=_ssh_diag(host_tools=_BTRFS_NOT_BTRFS_FS_HOST_TOOLS),
    )
    @patch("nbkp.preflight.volume_checks.check_volume_capabilities")
    def test_destination_not_btrfs_filesystem(
        self,
        mock_caps: MagicMock,
        _mock_ssh: MagicMock,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_active_sentinels(src, dst)

        src_vol = LocalVolume(slug="src", path=str(src))
        dst_vol = LocalVolume(slug="dst", path=str(dst))
        sync = SyncConfig(
            slug="s1",
            source="ep-notbtrfs-src",
            destination="ep-notbtrfs-dst",
        )
        config = Config(
            volumes={"src": src_vol, "dst": dst_vol},
            sync_endpoints={
                "ep-notbtrfs-src": SyncEndpoint(
                    slug="ep-notbtrfs-src",
                    volume="src",
                    subdir="data",
                ),
                "ep-notbtrfs-dst": SyncEndpoint(
                    slug="ep-notbtrfs-dst",
                    volume="dst",
                    subdir="backup",
                    btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
                ),
            },
            syncs={"s1": sync},
        )

        def caps_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            return _BTRFS_NOT_BTRFS_FS_CAPS if volume.slug == "dst" else _STUB_CAPS

        mock_caps.side_effect = caps_side_effect

        status = check_sync(sync, config)
        assert status.active is False
        assert (
            DestinationEndpointError.VOL_NOT_BTRFS
            in status.destination_endpoint_status.errors
        )

    @patch(
        "nbkp.preflight.checks.observe_ssh_endpoint",
        return_value=_ssh_diag(host_tools=_BTRFS_FULL_HOST_TOOLS),
    )
    @patch("nbkp.preflight.volume_checks.check_volume_capabilities")
    @patch("nbkp.remote.dispatch.subprocess.run")
    def test_destination_not_btrfs_subvolume(
        self,
        mock_subprocess: MagicMock,
        mock_caps: MagicMock,
        _mock_ssh: MagicMock,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_active_sentinels(src, dst)
        # staging/ must exist for the subvolume check to run
        (dst / "backup" / "staging").mkdir(parents=True)

        src_vol = LocalVolume(slug="src", path=str(src))
        dst_vol = LocalVolume(slug="dst", path=str(dst))
        sync = SyncConfig(
            slug="s1",
            source="ep-notsub-src",
            destination="ep-notsub-dst",
        )
        config = Config(
            volumes={"src": src_vol, "dst": dst_vol},
            sync_endpoints={
                "ep-notsub-src": SyncEndpoint(
                    slug="ep-notsub-src",
                    volume="src",
                    subdir="data",
                ),
                "ep-notsub-dst": SyncEndpoint(
                    slug="ep-notsub-dst",
                    volume="dst",
                    subdir="backup",
                    btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
                ),
            },
            syncs={"s1": sync},
        )

        def caps_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            return _BTRFS_FULL_CAPS if volume.slug == "dst" else _STUB_CAPS

        mock_caps.side_effect = caps_side_effect

        def subprocess_side_effect(cmd: list[str], **kwargs: object) -> MagicMock:
            if cmd[:3] == ["stat", "-c", "%i"]:
                return MagicMock(returncode=0, stdout="1234\n")
            return MagicMock(returncode=0)

        mock_subprocess.side_effect = subprocess_side_effect

        status = check_sync(sync, config)
        assert status.active is False
        assert (
            DestinationEndpointError.STAGING_NOT_BTRFS_SUBVOLUME
            in status.destination_endpoint_status.errors
        )

    @patch(
        "nbkp.preflight.checks.observe_ssh_endpoint",
        return_value=_ssh_diag(host_tools=_BTRFS_FULL_HOST_TOOLS),
    )
    @patch("nbkp.preflight.volume_checks.check_volume_capabilities")
    @patch("nbkp.remote.dispatch.subprocess.run")
    def test_destination_not_mounted_user_subvol_rm(
        self,
        mock_subprocess: MagicMock,
        mock_caps: MagicMock,
        _mock_ssh: MagicMock,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_active_sentinels(src, dst)
        (dst / "backup" / "staging").mkdir()
        (dst / "backup" / "snapshots").mkdir()

        src_vol = LocalVolume(slug="src", path=str(src))
        dst_vol = LocalVolume(slug="dst", path=str(dst))
        sync = SyncConfig(
            slug="s1",
            source="ep-mount-src",
            destination="ep-mount-dst",
        )
        config = Config(
            volumes={"src": src_vol, "dst": dst_vol},
            sync_endpoints={
                "ep-mount-src": SyncEndpoint(
                    slug="ep-mount-src",
                    volume="src",
                    subdir="data",
                ),
                "ep-mount-dst": SyncEndpoint(
                    slug="ep-mount-dst",
                    volume="dst",
                    subdir="backup",
                    btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
                ),
            },
            syncs={"s1": sync},
        )

        def caps_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            return _BTRFS_NO_SUBVOL_RM_CAPS if volume.slug == "dst" else _STUB_CAPS

        mock_caps.side_effect = caps_side_effect

        def subprocess_side_effect(cmd: list[str], **kwargs: object) -> MagicMock:
            if cmd[:3] == ["stat", "-c", "%i"]:
                return MagicMock(returncode=0, stdout="256\n")
            return MagicMock(returncode=0)

        mock_subprocess.side_effect = subprocess_side_effect

        status = check_sync(sync, config)
        assert status.active is False
        assert (
            DestinationEndpointError.VOL_NOT_MOUNTED_USER_SUBVOL_RM
            in status.destination_endpoint_status.errors
        )

    @patch(
        "nbkp.preflight.checks.observe_ssh_endpoint",
        return_value=_ssh_diag(host_tools=_BTRFS_FULL_HOST_TOOLS),
    )
    @patch("nbkp.preflight.volume_checks.check_volume_capabilities")
    @patch("nbkp.remote.dispatch.subprocess.run")
    def test_destination_latest_not_found(
        self,
        mock_subprocess: MagicMock,
        mock_caps: MagicMock,
        _mock_ssh: MagicMock,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_active_sentinels(src, dst)
        # snapshots exists but latest does not
        (dst / "backup" / "snapshots").mkdir()

        src_vol = LocalVolume(slug="src", path=str(src))
        dst_vol = LocalVolume(slug="dst", path=str(dst))
        sync = SyncConfig(
            slug="s1",
            source="ep-latest-src",
            destination="ep-latest-dst",
        )
        config = Config(
            volumes={"src": src_vol, "dst": dst_vol},
            sync_endpoints={
                "ep-latest-src": SyncEndpoint(
                    slug="ep-latest-src",
                    volume="src",
                    subdir="data",
                ),
                "ep-latest-dst": SyncEndpoint(
                    slug="ep-latest-dst",
                    volume="dst",
                    subdir="backup",
                    btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
                ),
            },
            syncs={"s1": sync},
        )

        def caps_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            return _BTRFS_FULL_CAPS if volume.slug == "dst" else _STUB_CAPS

        mock_caps.side_effect = caps_side_effect

        def subprocess_side_effect(cmd: list[str], **kwargs: object) -> MagicMock:
            if cmd[:3] == ["stat", "-c", "%i"]:
                return MagicMock(returncode=0, stdout="256\n")
            return MagicMock(returncode=0)

        mock_subprocess.side_effect = subprocess_side_effect

        status = check_sync(sync, config)
        assert status.active is False
        dst_ep_errors = status.destination_endpoint_status.errors
        assert DestinationEndpointError.STAGING_SUBVOL_NOT_FOUND in dst_ep_errors
        assert DestinationEndpointError.SNAPSHOTS_DIR_NOT_FOUND not in dst_ep_errors

    @patch(
        "nbkp.preflight.checks.observe_ssh_endpoint",
        return_value=_ssh_diag(host_tools=_BTRFS_FULL_HOST_TOOLS),
    )
    @patch("nbkp.preflight.volume_checks.check_volume_capabilities")
    @patch("nbkp.remote.dispatch.subprocess.run")
    def test_destination_snapshots_dir_not_found(
        self,
        mock_subprocess: MagicMock,
        mock_caps: MagicMock,
        _mock_ssh: MagicMock,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_active_sentinels(src, dst)
        # staging/ exists but snapshots/ does not
        (dst / "backup" / "staging").mkdir()

        src_vol = LocalVolume(slug="src", path=str(src))
        dst_vol = LocalVolume(slug="dst", path=str(dst))
        sync = SyncConfig(
            slug="s1",
            source="ep-snapdir-src",
            destination="ep-snapdir-dst",
        )
        config = Config(
            volumes={"src": src_vol, "dst": dst_vol},
            sync_endpoints={
                "ep-snapdir-src": SyncEndpoint(
                    slug="ep-snapdir-src",
                    volume="src",
                    subdir="data",
                ),
                "ep-snapdir-dst": SyncEndpoint(
                    slug="ep-snapdir-dst",
                    volume="dst",
                    subdir="backup",
                    btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
                ),
            },
            syncs={"s1": sync},
        )

        def caps_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            return _BTRFS_FULL_CAPS if volume.slug == "dst" else _STUB_CAPS

        mock_caps.side_effect = caps_side_effect

        def subprocess_side_effect(cmd: list[str], **kwargs: object) -> MagicMock:
            if cmd[:3] == ["stat", "-c", "%i"]:
                return MagicMock(returncode=0, stdout="256\n")
            return MagicMock(returncode=0)

        mock_subprocess.side_effect = subprocess_side_effect

        status = check_sync(sync, config)
        assert status.active is False
        dst_ep_errors = status.destination_endpoint_status.errors
        assert DestinationEndpointError.SNAPSHOTS_DIR_NOT_FOUND in dst_ep_errors
        assert DestinationEndpointError.STAGING_SUBVOL_NOT_FOUND not in dst_ep_errors

    @patch(
        "nbkp.preflight.checks.observe_ssh_endpoint",
        return_value=_ssh_diag(host_tools=_BTRFS_FULL_HOST_TOOLS),
    )
    @patch("nbkp.preflight.volume_checks.check_volume_capabilities")
    @patch("nbkp.remote.dispatch.subprocess.run")
    def test_destination_latest_and_snapshots_both_missing(
        self,
        mock_subprocess: MagicMock,
        mock_caps: MagicMock,
        _mock_ssh: MagicMock,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_active_sentinels(src, dst)
        # neither latest nor snapshots exist

        src_vol = LocalVolume(slug="src", path=str(src))
        dst_vol = LocalVolume(slug="dst", path=str(dst))
        sync = SyncConfig(
            slug="s1",
            source="ep-both-src",
            destination="ep-both-dst",
        )
        config = Config(
            volumes={"src": src_vol, "dst": dst_vol},
            sync_endpoints={
                "ep-both-src": SyncEndpoint(
                    slug="ep-both-src",
                    volume="src",
                    subdir="data",
                ),
                "ep-both-dst": SyncEndpoint(
                    slug="ep-both-dst",
                    volume="dst",
                    subdir="backup",
                    btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
                ),
            },
            syncs={"s1": sync},
        )

        def caps_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            return _BTRFS_FULL_CAPS if volume.slug == "dst" else _STUB_CAPS

        mock_caps.side_effect = caps_side_effect

        def subprocess_side_effect(cmd: list[str], **kwargs: object) -> MagicMock:
            if cmd[:3] == ["stat", "-c", "%i"]:
                return MagicMock(returncode=0, stdout="256\n")
            return MagicMock(returncode=0)

        mock_subprocess.side_effect = subprocess_side_effect

        status = check_sync(sync, config)
        assert status.active is False
        dst_ep_errors = status.destination_endpoint_status.errors
        assert DestinationEndpointError.STAGING_SUBVOL_NOT_FOUND in dst_ep_errors
        assert DestinationEndpointError.SNAPSHOTS_DIR_NOT_FOUND in dst_ep_errors

    @patch(
        "nbkp.preflight.checks.observe_ssh_endpoint",
        return_value=_ssh_diag(),
    )
    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    def test_btrfs_check_skipped_when_not_enabled(
        self,
        _mock_caps: MagicMock,
        _mock_ssh: MagicMock,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_active_sentinels(src, dst)

        config, sync = self._make_config(src, dst)

        status = check_sync(sync, config)
        assert status.active is True
        assert status.errors == []

    @patch(
        "nbkp.preflight.checks.observe_ssh_endpoint",
        return_value=_ssh_diag(host_tools=_NO_RSYNC_HOST_TOOLS),
    )
    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_NO_RSYNC_CAPS,
    )
    def test_multiple_failures_accumulated(
        self, _mock_caps: MagicMock, _mock_ssh: MagicMock, tmp_path: Path
    ) -> None:
        """Rsync missing on local volume: SSH endpoint reports the error,
        but the volume is still probed (local volumes don't cascade
        SSH_ENDPOINT_INACTIVE).  The sync is inactive because the source
        endpoint sentinel is missing."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        (src / ".nbkp-vol").touch()
        (dst / ".nbkp-vol").touch()
        (src / "data").mkdir()
        # No .nbkp-src sentinel
        (dst / "backup").mkdir()
        (dst / "backup" / ".nbkp-dst").touch()

        config, sync = self._make_config(src, dst)

        status = check_sync(sync, config)
        assert status.active is False
        ssh_errors = (
            status.source_endpoint_status.volume_status.ssh_endpoint_status.errors
        )
        assert SshEndpointError.RSYNC_NOT_FOUND in ssh_errors
        # Local volume is still probed despite SSH endpoint errors —
        # no SSH_ENDPOINT_INACTIVE cascade for local volumes
        assert status.source_endpoint_status.volume_status.active is True
        # Source endpoint is inactive because .nbkp-src sentinel is missing
        assert SourceEndpointError.SENTINEL_NOT_FOUND in (
            status.source_endpoint_status.errors
        )

    @patch(
        "nbkp.preflight.checks.observe_ssh_endpoint",
        return_value=_ssh_diag(),
    )
    def test_both_volumes_unavailable(
        self, _mock_ssh: MagicMock, tmp_path: Path
    ) -> None:
        """Both source and destination unavailable."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        # No .nbkp-vol sentinel files, so volumes are inactive

        config, sync = self._make_config(src, dst)

        status = check_sync(sync, config)
        assert status.active is False
        src_vol_errors = status.source_endpoint_status.volume_status.errors
        dst_vol_errors = status.destination_endpoint_status.volume_status.errors
        assert VolumeError.SENTINEL_NOT_FOUND in src_vol_errors
        assert VolumeError.SENTINEL_NOT_FOUND in dst_vol_errors


class TestCheckSyncRemoteCommands:
    @patch(
        "nbkp.preflight.checks.observe_ssh_endpoint",
    )
    @patch("nbkp.preflight.volume_checks.check_volume_capabilities")
    @patch("nbkp.remote.dispatch.run_remote_command")
    def test_rsync_not_found_on_remote_source(
        self,
        mock_run: MagicMock,
        mock_caps: MagicMock,
        mock_ssh_ep: MagicMock,
        tmp_path: Path,
    ) -> None:
        dst = tmp_path / "dst"
        dst.mkdir()
        (dst / ".nbkp-vol").touch()
        (dst / "backup").mkdir()
        (dst / "backup" / ".nbkp-dst").touch()

        src_server = SshEndpoint(slug="src-server", host="src.local")
        src_vol = RemoteVolume(
            slug="src",
            ssh_endpoint="src-server",
            path="/data",
        )
        dst_vol = LocalVolume(slug="dst", path=str(dst))
        sync = SyncConfig(
            slug="s1",
            source="ep-rsrc",
            destination="ep-rdst",
        )
        config = Config(
            ssh_endpoints={"src-server": src_server},
            volumes={"src": src_vol, "dst": dst_vol},
            sync_endpoints={
                "ep-rsrc": SyncEndpoint(slug="ep-rsrc", volume="src", subdir="data"),
                "ep-rdst": SyncEndpoint(slug="ep-rdst", volume="dst", subdir="backup"),
            },
            syncs={"s1": sync},
        )

        def ssh_ep_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            if isinstance(volume, RemoteVolume):
                return _ssh_diag(host_tools=_NO_RSYNC_HOST_TOOLS, ssh_reachable=True)
            return _ssh_diag()

        mock_ssh_ep.side_effect = ssh_ep_side_effect

        def caps_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            return _NO_RSYNC_CAPS if volume.slug == "src" else _STUB_CAPS

        mock_caps.side_effect = caps_side_effect

        def remote_side_effect(
            server: SshEndpoint,
            cmd: list[str],
            proxy_chain: list[SshEndpoint] | None = None,
            input: str | None = None,
        ) -> MagicMock:
            if cmd == ["test", "-f", "/data/data/.nbkp-src"]:
                return MagicMock(returncode=0)
            return MagicMock(returncode=0)

        mock_run.side_effect = remote_side_effect

        status = check_sync(sync, config, _make_resolved(config))
        assert status.active is False
        ssh_errors = (
            status.source_endpoint_status.volume_status.ssh_endpoint_status.errors
        )
        assert SshEndpointError.RSYNC_NOT_FOUND in ssh_errors

    @patch("nbkp.preflight.checks.observe_ssh_endpoint")
    @patch("nbkp.preflight.volume_checks.check_volume_capabilities")
    @patch("nbkp.remote.dispatch.run_remote_command")
    def test_rsync_not_found_on_remote_destination(
        self,
        mock_run: MagicMock,
        mock_caps: MagicMock,
        mock_ssh_ep: MagicMock,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / ".nbkp-vol").touch()
        (src / "data").mkdir()
        (src / "data" / ".nbkp-src").touch()

        dst_server = SshEndpoint(slug="dst-server", host="dst.local")
        src_vol = LocalVolume(slug="src", path=str(src))
        dst_vol = RemoteVolume(
            slug="dst",
            ssh_endpoint="dst-server",
            path="/backup",
        )
        sync = SyncConfig(
            slug="s1",
            source="ep-rdsrc",
            destination="ep-rddst",
        )
        config = Config(
            ssh_endpoints={"dst-server": dst_server},
            volumes={"src": src_vol, "dst": dst_vol},
            sync_endpoints={
                "ep-rdsrc": SyncEndpoint(slug="ep-rdsrc", volume="src", subdir="data"),
                "ep-rddst": SyncEndpoint(
                    slug="ep-rddst", volume="dst", subdir="backup"
                ),
            },
            syncs={"s1": sync},
        )

        def ssh_ep_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            if isinstance(volume, RemoteVolume):
                return _ssh_diag(host_tools=_NO_RSYNC_HOST_TOOLS, ssh_reachable=True)
            return _ssh_diag()

        mock_ssh_ep.side_effect = ssh_ep_side_effect

        def caps_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            return _NO_RSYNC_CAPS if volume.slug == "dst" else _STUB_CAPS

        mock_caps.side_effect = caps_side_effect

        def remote_side_effect(
            server: SshEndpoint,
            cmd: list[str],
            proxy_chain: list[SshEndpoint] | None = None,
            input: str | None = None,
        ) -> MagicMock:
            if cmd == [
                "test",
                "-f",
                "/backup/backup/.nbkp-dst",
            ]:
                return MagicMock(returncode=0)
            return MagicMock(returncode=0)

        mock_run.side_effect = remote_side_effect

        status = check_sync(sync, config, _make_resolved(config))
        assert status.active is False
        ssh_errors = (
            status.destination_endpoint_status.volume_status.ssh_endpoint_status.errors
        )
        assert SshEndpointError.RSYNC_NOT_FOUND in ssh_errors

    @patch("nbkp.preflight.checks.observe_ssh_endpoint")
    @patch("nbkp.preflight.volume_checks.check_volume_capabilities")
    @patch("nbkp.remote.dispatch.run_remote_command")
    def test_btrfs_not_found_on_remote_destination(
        self,
        mock_run: MagicMock,
        mock_caps: MagicMock,
        mock_ssh_ep: MagicMock,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / ".nbkp-vol").touch()
        (src / "data").mkdir()
        (src / "data" / ".nbkp-src").touch()

        dst_server = SshEndpoint(slug="dst-server", host="dst.local")
        src_vol = LocalVolume(slug="src", path=str(src))
        dst_vol = RemoteVolume(
            slug="dst",
            ssh_endpoint="dst-server",
            path="/backup",
        )
        sync = SyncConfig(
            slug="s1",
            source="ep-rbtrfs-src",
            destination="ep-rbtrfs-dst",
        )
        config = Config(
            ssh_endpoints={"dst-server": dst_server},
            volumes={"src": src_vol, "dst": dst_vol},
            sync_endpoints={
                "ep-rbtrfs-src": SyncEndpoint(
                    slug="ep-rbtrfs-src",
                    volume="src",
                    subdir="data",
                ),
                "ep-rbtrfs-dst": SyncEndpoint(
                    slug="ep-rbtrfs-dst",
                    volume="dst",
                    subdir="backup",
                    btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
                ),
            },
            syncs={"s1": sync},
        )

        def ssh_ep_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            if isinstance(volume, RemoteVolume):
                return _ssh_diag(
                    host_tools=_BTRFS_NO_BTRFS_CMD_HOST_TOOLS, ssh_reachable=True
                )
            return _ssh_diag()

        mock_ssh_ep.side_effect = ssh_ep_side_effect

        def caps_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            return _BTRFS_NO_BTRFS_CMD_CAPS if volume.slug == "dst" else _STUB_CAPS

        mock_caps.side_effect = caps_side_effect

        def remote_side_effect(
            server: SshEndpoint,
            cmd: list[str],
            proxy_chain: list[SshEndpoint] | None = None,
            input: str | None = None,
        ) -> MagicMock:
            if cmd == [
                "test",
                "-f",
                "/backup/backup/.nbkp-dst",
            ]:
                return MagicMock(returncode=0)
            return MagicMock(returncode=0, stdout="/dev/null\n")

        mock_run.side_effect = remote_side_effect

        status = check_sync(sync, config, _make_resolved(config))
        assert status.active is False
        ssh_errors = (
            status.destination_endpoint_status.volume_status.ssh_endpoint_status.errors
        )
        assert SshEndpointError.BTRFS_NOT_FOUND in ssh_errors

    @patch("nbkp.preflight.checks.observe_ssh_endpoint")
    @patch("nbkp.preflight.volume_checks.check_volume_capabilities")
    @patch("nbkp.remote.dispatch.run_remote_command")
    def test_destination_not_btrfs_on_remote(
        self,
        mock_run: MagicMock,
        mock_caps: MagicMock,
        mock_ssh_ep: MagicMock,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / ".nbkp-vol").touch()
        (src / "data").mkdir()
        (src / "data" / ".nbkp-src").touch()

        dst_server = SshEndpoint(slug="dst-server", host="dst.local")
        src_vol = LocalVolume(slug="src", path=str(src))
        dst_vol = RemoteVolume(
            slug="dst",
            ssh_endpoint="dst-server",
            path="/backup",
        )
        sync = SyncConfig(
            slug="s1",
            source="ep-rnotbtrfs-src",
            destination="ep-rnotbtrfs-dst",
        )
        config = Config(
            ssh_endpoints={"dst-server": dst_server},
            volumes={"src": src_vol, "dst": dst_vol},
            sync_endpoints={
                "ep-rnotbtrfs-src": SyncEndpoint(
                    slug="ep-rnotbtrfs-src",
                    volume="src",
                    subdir="data",
                ),
                "ep-rnotbtrfs-dst": SyncEndpoint(
                    slug="ep-rnotbtrfs-dst",
                    volume="dst",
                    subdir="backup",
                    btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
                ),
            },
            syncs={"s1": sync},
        )

        def ssh_ep_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            if isinstance(volume, RemoteVolume):
                return _ssh_diag(host_tools=_BTRFS_FULL_HOST_TOOLS, ssh_reachable=True)
            return _ssh_diag()

        mock_ssh_ep.side_effect = ssh_ep_side_effect

        def caps_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            return _BTRFS_NOT_BTRFS_FS_CAPS if volume.slug == "dst" else _STUB_CAPS

        mock_caps.side_effect = caps_side_effect

        def remote_side_effect(
            server: SshEndpoint,
            cmd: list[str],
            proxy_chain: list[SshEndpoint] | None = None,
            input: str | None = None,
        ) -> MagicMock:
            if cmd == [
                "test",
                "-f",
                "/backup/backup/.nbkp-dst",
            ]:
                return MagicMock(returncode=0)
            return MagicMock(returncode=0, stdout="/dev/null\n")

        mock_run.side_effect = remote_side_effect

        status = check_sync(sync, config, _make_resolved(config))
        assert status.active is False
        assert (
            DestinationEndpointError.VOL_NOT_BTRFS
            in status.destination_endpoint_status.errors
        )

    @patch("nbkp.preflight.checks.observe_ssh_endpoint")
    @patch("nbkp.preflight.volume_checks.check_volume_capabilities")
    @patch("nbkp.remote.dispatch.run_remote_command")
    def test_destination_not_subvolume_on_remote(
        self,
        mock_run: MagicMock,
        mock_caps: MagicMock,
        mock_ssh_ep: MagicMock,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / ".nbkp-vol").touch()
        (src / "data").mkdir()
        (src / "data" / ".nbkp-src").touch()

        dst_server = SshEndpoint(slug="dst-server", host="dst.local")
        src_vol = LocalVolume(slug="src", path=str(src))
        dst_vol = RemoteVolume(
            slug="dst",
            ssh_endpoint="dst-server",
            path="/backup",
        )
        sync = SyncConfig(
            slug="s1",
            source="ep-rnotsub-src",
            destination="ep-rnotsub-dst",
        )
        config = Config(
            ssh_endpoints={"dst-server": dst_server},
            volumes={"src": src_vol, "dst": dst_vol},
            sync_endpoints={
                "ep-rnotsub-src": SyncEndpoint(
                    slug="ep-rnotsub-src",
                    volume="src",
                    subdir="backup",
                ),
                "ep-rnotsub-dst": SyncEndpoint(
                    slug="ep-rnotsub-dst",
                    volume="dst",
                    subdir="backup",
                    btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
                ),
            },
            syncs={"s1": sync},
        )

        def ssh_ep_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            if isinstance(volume, RemoteVolume):
                return _ssh_diag(host_tools=_BTRFS_FULL_HOST_TOOLS, ssh_reachable=True)
            return _ssh_diag()

        mock_ssh_ep.side_effect = ssh_ep_side_effect

        def caps_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            return _BTRFS_FULL_CAPS if volume.slug == "dst" else _STUB_CAPS

        mock_caps.side_effect = caps_side_effect

        def remote_side_effect(
            server: SshEndpoint,
            cmd: list[str],
            proxy_chain: list[SshEndpoint] | None = None,
            input: str | None = None,
        ) -> MagicMock:
            if cmd == [
                "test",
                "-f",
                "/backup/backup/.nbkp-dst",
            ]:
                return MagicMock(returncode=0)
            if cmd == [
                "stat",
                "-c",
                "%i",
                "/backup/backup",
            ]:
                return MagicMock(returncode=0, stdout="1234\n")
            return MagicMock(returncode=0, stdout="/dev/null\n")

        mock_run.side_effect = remote_side_effect

        status = check_sync(sync, config, _make_resolved(config))
        assert status.active is False
        assert (
            DestinationEndpointError.STAGING_NOT_BTRFS_SUBVOLUME
            in status.destination_endpoint_status.errors
        )

    @patch("nbkp.preflight.checks.observe_ssh_endpoint")
    @patch("nbkp.preflight.volume_checks.check_volume_capabilities")
    @patch("nbkp.remote.dispatch.run_remote_command")
    def test_destination_not_mounted_user_subvol_rm_on_remote(
        self,
        mock_run: MagicMock,
        mock_caps: MagicMock,
        mock_ssh_ep: MagicMock,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / ".nbkp-vol").touch()
        (src / "data").mkdir()
        (src / "data" / ".nbkp-src").touch()

        dst_server = SshEndpoint(slug="dst-server", host="dst.local")
        src_vol = LocalVolume(slug="src", path=str(src))
        dst_vol = RemoteVolume(
            slug="dst",
            ssh_endpoint="dst-server",
            path="/backup",
        )
        sync = SyncConfig(
            slug="s1",
            source="ep-rmount-src",
            destination="ep-rmount-dst",
        )
        config = Config(
            ssh_endpoints={"dst-server": dst_server},
            volumes={"src": src_vol, "dst": dst_vol},
            sync_endpoints={
                "ep-rmount-src": SyncEndpoint(
                    slug="ep-rmount-src",
                    volume="src",
                    subdir="data",
                ),
                "ep-rmount-dst": SyncEndpoint(
                    slug="ep-rmount-dst",
                    volume="dst",
                    subdir="backup",
                    btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
                ),
            },
            syncs={"s1": sync},
        )

        def ssh_ep_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            if isinstance(volume, RemoteVolume):
                return _ssh_diag(host_tools=_BTRFS_FULL_HOST_TOOLS, ssh_reachable=True)
            return _ssh_diag()

        mock_ssh_ep.side_effect = ssh_ep_side_effect

        def caps_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            return _BTRFS_NO_SUBVOL_RM_CAPS if volume.slug == "dst" else _STUB_CAPS

        mock_caps.side_effect = caps_side_effect

        def remote_side_effect(
            server: SshEndpoint,
            cmd: list[str],
            proxy_chain: list[SshEndpoint] | None = None,
            input: str | None = None,
        ) -> MagicMock:
            if cmd == [
                "test",
                "-f",
                "/backup/backup/.nbkp-dst",
            ]:
                return MagicMock(returncode=0)
            if cmd == [
                "stat",
                "-c",
                "%i",
                "/backup/backup",
            ]:
                return MagicMock(returncode=0, stdout="256\n")
            return MagicMock(returncode=0, stdout="/dev/null\n")

        mock_run.side_effect = remote_side_effect

        status = check_sync(sync, config, _make_resolved(config))
        assert status.active is False
        assert (
            DestinationEndpointError.VOL_NOT_MOUNTED_USER_SUBVOL_RM
            in status.destination_endpoint_status.errors
        )

    @patch("nbkp.preflight.checks.observe_ssh_endpoint")
    @patch("nbkp.preflight.volume_checks.check_volume_capabilities")
    @patch("nbkp.remote.dispatch.run_remote_command")
    def test_stat_not_found_on_remote_destination(
        self,
        mock_run: MagicMock,
        mock_caps: MagicMock,
        mock_ssh_ep: MagicMock,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / ".nbkp-vol").touch()
        (src / "data").mkdir()
        (src / "data" / ".nbkp-src").touch()

        dst_server = SshEndpoint(slug="dst-server", host="dst.local")
        src_vol = LocalVolume(slug="src", path=str(src))
        dst_vol = RemoteVolume(
            slug="dst",
            ssh_endpoint="dst-server",
            path="/backup",
        )
        sync = SyncConfig(
            slug="s1",
            source="ep-rstat-src",
            destination="ep-rstat-dst",
        )
        config = Config(
            ssh_endpoints={"dst-server": dst_server},
            volumes={"src": src_vol, "dst": dst_vol},
            sync_endpoints={
                "ep-rstat-src": SyncEndpoint(
                    slug="ep-rstat-src",
                    volume="src",
                    subdir="data",
                ),
                "ep-rstat-dst": SyncEndpoint(
                    slug="ep-rstat-dst",
                    volume="dst",
                    subdir="backup",
                    btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
                ),
            },
            syncs={"s1": sync},
        )

        def ssh_ep_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            if isinstance(volume, RemoteVolume):
                return _ssh_diag(
                    host_tools=_BTRFS_NO_STAT_HOST_TOOLS, ssh_reachable=True
                )
            return _ssh_diag()

        mock_ssh_ep.side_effect = ssh_ep_side_effect

        def caps_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            return _BTRFS_NO_STAT_CAPS if volume.slug == "dst" else _STUB_CAPS

        mock_caps.side_effect = caps_side_effect

        def remote_side_effect(
            server: SshEndpoint,
            cmd: list[str],
            proxy_chain: list[SshEndpoint] | None = None,
            input: str | None = None,
        ) -> MagicMock:
            if cmd == [
                "test",
                "-f",
                "/backup/backup/.nbkp-dst",
            ]:
                return MagicMock(returncode=0)
            return MagicMock(returncode=0, stdout="/dev/null\n")

        mock_run.side_effect = remote_side_effect

        status = check_sync(sync, config, _make_resolved(config))
        assert status.active is False
        ssh_errors = (
            status.destination_endpoint_status.volume_status.ssh_endpoint_status.errors
        )
        assert SshEndpointError.STAT_NOT_FOUND in ssh_errors
        assert (
            DestinationEndpointError.VOL_NOT_BTRFS
            not in status.destination_endpoint_status.errors
        )

    @patch("nbkp.preflight.checks.observe_ssh_endpoint")
    @patch("nbkp.preflight.volume_checks.check_volume_capabilities")
    @patch("nbkp.remote.dispatch.run_remote_command")
    def test_findmnt_not_found_on_remote_destination(
        self,
        mock_run: MagicMock,
        mock_caps: MagicMock,
        mock_ssh_ep: MagicMock,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / ".nbkp-vol").touch()
        (src / "data").mkdir()
        (src / "data" / ".nbkp-src").touch()

        dst_server = SshEndpoint(slug="dst-server", host="dst.local")
        src_vol = LocalVolume(slug="src", path=str(src))
        dst_vol = RemoteVolume(
            slug="dst",
            ssh_endpoint="dst-server",
            path="/backup",
        )
        sync = SyncConfig(
            slug="s1",
            source="ep-rfindmnt-src",
            destination="ep-rfindmnt-dst",
        )
        config = Config(
            ssh_endpoints={"dst-server": dst_server},
            volumes={"src": src_vol, "dst": dst_vol},
            sync_endpoints={
                "ep-rfindmnt-src": SyncEndpoint(
                    slug="ep-rfindmnt-src",
                    volume="src",
                    subdir="data",
                ),
                "ep-rfindmnt-dst": SyncEndpoint(
                    slug="ep-rfindmnt-dst",
                    volume="dst",
                    subdir="backup",
                    btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
                ),
            },
            syncs={"s1": sync},
        )

        def ssh_ep_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            if isinstance(volume, RemoteVolume):
                return _ssh_diag(
                    host_tools=_BTRFS_NO_FINDMNT_HOST_TOOLS, ssh_reachable=True
                )
            return _ssh_diag()

        mock_ssh_ep.side_effect = ssh_ep_side_effect

        def caps_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            return _BTRFS_NO_FINDMNT_CAPS if volume.slug == "dst" else _STUB_CAPS

        mock_caps.side_effect = caps_side_effect

        def remote_side_effect(
            server: SshEndpoint,
            cmd: list[str],
            proxy_chain: list[SshEndpoint] | None = None,
            input: str | None = None,
        ) -> MagicMock:
            if cmd == [
                "test",
                "-f",
                "/backup/backup/.nbkp-dst",
            ]:
                return MagicMock(returncode=0)
            if cmd == [
                "stat",
                "-c",
                "%i",
                "/backup/backup",
            ]:
                return MagicMock(returncode=0, stdout="256\n")
            if cmd == ["test", "-d", "/backup/backup/staging"]:
                return MagicMock(returncode=0)
            if cmd == ["test", "-d", "/backup/backup/snapshots"]:
                return MagicMock(returncode=0)
            return MagicMock(returncode=0, stdout="/dev/null\n")

        mock_run.side_effect = remote_side_effect

        status = check_sync(sync, config, _make_resolved(config))
        assert status.active is False
        ssh_errors = (
            status.destination_endpoint_status.volume_status.ssh_endpoint_status.errors
        )
        assert SshEndpointError.FINDMNT_NOT_FOUND in ssh_errors
        assert (
            DestinationEndpointError.VOL_NOT_MOUNTED_USER_SUBVOL_RM
            not in status.destination_endpoint_status.errors
        )

    @patch("nbkp.preflight.checks.observe_ssh_endpoint")
    @patch("nbkp.preflight.volume_checks.check_volume_capabilities")
    @patch("nbkp.remote.dispatch.run_remote_command")
    def test_destination_latest_not_found_on_remote(
        self,
        mock_run: MagicMock,
        mock_caps: MagicMock,
        mock_ssh_ep: MagicMock,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / ".nbkp-vol").touch()
        (src / "data").mkdir()
        (src / "data" / ".nbkp-src").touch()

        dst_server = SshEndpoint(slug="dst-server", host="dst.local")
        src_vol = LocalVolume(slug="src", path=str(src))
        dst_vol = RemoteVolume(
            slug="dst",
            ssh_endpoint="dst-server",
            path="/backup",
        )
        sync = SyncConfig(
            slug="s1",
            source="ep-rlatest-src",
            destination="ep-rlatest-dst",
        )
        config = Config(
            ssh_endpoints={"dst-server": dst_server},
            volumes={"src": src_vol, "dst": dst_vol},
            sync_endpoints={
                "ep-rlatest-src": SyncEndpoint(
                    slug="ep-rlatest-src",
                    volume="src",
                    subdir="data",
                ),
                "ep-rlatest-dst": SyncEndpoint(
                    slug="ep-rlatest-dst",
                    volume="dst",
                    subdir="backup",
                    btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
                ),
            },
            syncs={"s1": sync},
        )

        def ssh_ep_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            if isinstance(volume, RemoteVolume):
                return _ssh_diag(host_tools=_BTRFS_FULL_HOST_TOOLS, ssh_reachable=True)
            return _ssh_diag()

        mock_ssh_ep.side_effect = ssh_ep_side_effect

        def caps_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            return _BTRFS_FULL_CAPS if volume.slug == "dst" else _STUB_CAPS

        mock_caps.side_effect = caps_side_effect

        def remote_side_effect(
            server: SshEndpoint,
            cmd: list[str],
            proxy_chain: list[SshEndpoint] | None = None,
            input: str | None = None,
        ) -> MagicMock:
            if cmd == [
                "test",
                "-f",
                "/backup/backup/.nbkp-dst",
            ]:
                return MagicMock(returncode=0)
            if cmd == [
                "stat",
                "-c",
                "%i",
                "/backup/backup",
            ]:
                return MagicMock(returncode=0, stdout="256\n")
            if cmd == [
                "test",
                "-d",
                "/backup/backup/staging",
            ]:
                return MagicMock(returncode=1)
            if cmd == [
                "test",
                "-d",
                "/backup/backup/snapshots",
            ]:
                return MagicMock(returncode=0)
            return MagicMock(returncode=0, stdout="/dev/null\n")

        mock_run.side_effect = remote_side_effect

        status = check_sync(sync, config, _make_resolved(config))
        assert status.active is False
        assert (
            DestinationEndpointError.STAGING_SUBVOL_NOT_FOUND
            in status.destination_endpoint_status.errors
        )
        assert (
            DestinationEndpointError.SNAPSHOTS_DIR_NOT_FOUND
            not in status.destination_endpoint_status.errors
        )

    @patch("nbkp.preflight.checks.observe_ssh_endpoint")
    @patch("nbkp.preflight.volume_checks.check_volume_capabilities")
    @patch("nbkp.remote.dispatch.run_remote_command")
    def test_destination_snapshots_dir_not_found_on_remote(
        self,
        mock_run: MagicMock,
        mock_caps: MagicMock,
        mock_ssh_ep: MagicMock,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / ".nbkp-vol").touch()
        (src / "data").mkdir()
        (src / "data" / ".nbkp-src").touch()

        dst_server = SshEndpoint(slug="dst-server", host="dst.local")
        src_vol = LocalVolume(slug="src", path=str(src))
        dst_vol = RemoteVolume(
            slug="dst",
            ssh_endpoint="dst-server",
            path="/backup",
        )
        sync = SyncConfig(
            slug="s1",
            source="ep-rsnapdir-src",
            destination="ep-rsnapdir-dst",
        )
        config = Config(
            ssh_endpoints={"dst-server": dst_server},
            volumes={"src": src_vol, "dst": dst_vol},
            sync_endpoints={
                "ep-rsnapdir-src": SyncEndpoint(
                    slug="ep-rsnapdir-src",
                    volume="src",
                    subdir="data",
                ),
                "ep-rsnapdir-dst": SyncEndpoint(
                    slug="ep-rsnapdir-dst",
                    volume="dst",
                    subdir="backup",
                    btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
                ),
            },
            syncs={"s1": sync},
        )

        def ssh_ep_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            if isinstance(volume, RemoteVolume):
                return _ssh_diag(host_tools=_BTRFS_FULL_HOST_TOOLS, ssh_reachable=True)
            return _ssh_diag()

        mock_ssh_ep.side_effect = ssh_ep_side_effect

        def caps_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            return _BTRFS_FULL_CAPS if volume.slug == "dst" else _STUB_CAPS

        mock_caps.side_effect = caps_side_effect

        def remote_side_effect(
            server: SshEndpoint,
            cmd: list[str],
            proxy_chain: list[SshEndpoint] | None = None,
            input: str | None = None,
        ) -> MagicMock:
            if cmd == [
                "test",
                "-f",
                "/backup/backup/.nbkp-dst",
            ]:
                return MagicMock(returncode=0)
            if cmd == [
                "stat",
                "-c",
                "%i",
                "/backup/backup",
            ]:
                return MagicMock(returncode=0, stdout="256\n")
            if cmd == [
                "test",
                "-d",
                "/backup/backup/staging",
            ]:
                return MagicMock(returncode=0)
            if cmd == [
                "test",
                "-d",
                "/backup/backup/snapshots",
            ]:
                return MagicMock(returncode=1)
            return MagicMock(returncode=0, stdout="/dev/null\n")

        mock_run.side_effect = remote_side_effect

        status = check_sync(sync, config, _make_resolved(config))
        assert status.active is False
        assert (
            DestinationEndpointError.SNAPSHOTS_DIR_NOT_FOUND
            in status.destination_endpoint_status.errors
        )
        assert (
            DestinationEndpointError.STAGING_SUBVOL_NOT_FOUND
            not in status.destination_endpoint_status.errors
        )


class TestCheckAllSyncs:
    @patch(
        "nbkp.preflight.checks.observe_ssh_endpoint",
        return_value=_ssh_diag(),
    )
    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    def test_check_all(
        self,
        _mock_caps: MagicMock,
        _mock_ssh: MagicMock,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        (src / ".nbkp-vol").touch()
        (dst / ".nbkp-vol").touch()
        (src / ".nbkp-src").touch()
        (dst / ".nbkp-dst").touch()

        src_vol = LocalVolume(slug="src", path=str(src))
        dst_vol = LocalVolume(slug="dst", path=str(dst))
        sync = SyncConfig(
            slug="s1",
            source="ep-all-src",
            destination="ep-all-dst",
        )
        config = Config(
            volumes={"src": src_vol, "dst": dst_vol},
            sync_endpoints={
                "ep-all-src": SyncEndpoint(slug="ep-all-src", volume="src"),
                "ep-all-dst": SyncEndpoint(slug="ep-all-dst", volume="dst"),
            },
            syncs={"s1": sync},
        )

        result = check_all_syncs(config)
        assert result.volume_statuses["src"].active is True
        assert result.volume_statuses["dst"].active is True
        assert result.sync_statuses["s1"].active is True

    @patch(
        "nbkp.preflight.checks.observe_ssh_endpoint",
        return_value=_ssh_diag(),
    )
    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    def test_only_syncs_filters_syncs_and_volumes(
        self,
        _mock_caps: MagicMock,
        _mock_ssh: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When only_syncs is given, only those syncs and their
        referenced volumes are checked."""
        src1 = tmp_path / "src1"
        dst1 = tmp_path / "dst1"
        src2 = tmp_path / "src2"
        dst2 = tmp_path / "dst2"
        for d in (src1, dst1, src2, dst2):
            d.mkdir()
            (d / ".nbkp-vol").touch()
        (src1 / ".nbkp-src").touch()
        (dst1 / ".nbkp-dst").touch()
        (src2 / ".nbkp-src").touch()
        (dst2 / ".nbkp-dst").touch()

        config = Config(
            volumes={
                "src1": LocalVolume(slug="src1", path=str(src1)),
                "dst1": LocalVolume(slug="dst1", path=str(dst1)),
                "src2": LocalVolume(slug="src2", path=str(src2)),
                "dst2": LocalVolume(slug="dst2", path=str(dst2)),
            },
            sync_endpoints={
                "ep-s1-src": SyncEndpoint(slug="ep-s1-src", volume="src1"),
                "ep-s1-dst": SyncEndpoint(slug="ep-s1-dst", volume="dst1"),
                "ep-s2-src": SyncEndpoint(slug="ep-s2-src", volume="src2"),
                "ep-s2-dst": SyncEndpoint(slug="ep-s2-dst", volume="dst2"),
            },
            syncs={
                "s1": SyncConfig(
                    slug="s1",
                    source="ep-s1-src",
                    destination="ep-s1-dst",
                ),
                "s2": SyncConfig(
                    slug="s2",
                    source="ep-s2-src",
                    destination="ep-s2-dst",
                ),
            },
        )

        result = check_all_syncs(config, only_syncs=["s1"])
        assert set(result.sync_statuses.keys()) == {"s1"}
        assert set(result.volume_statuses.keys()) == {"src1", "dst1"}
        assert result.sync_statuses["s1"].active is True

    @pytest.mark.parametrize("only_syncs", [None, ["s1"]])
    @patch("nbkp.preflight.checks.observe_standalone_endpoint")
    @patch("nbkp.preflight.checks.observe_ssh_endpoint", return_value=_ssh_diag())
    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    def test_step_count_matches_reported_steps(
        self,
        _mock_caps: MagicMock,
        _mock_ssh: MagicMock,
        mock_standalone: MagicMock,
        only_syncs: list[str] | None,
        tmp_path: Path,
    ) -> None:
        mock_standalone.return_value = _REACHABLE_SSH_DIAG
        config = Config(
            ssh_endpoints={"orphan": SshEndpoint(slug="orphan", host="orphan")},
            volumes={
                "a": LocalVolume(slug="a", path=str(tmp_path / "a")),
                "b": LocalVolume(slug="b", path=str(tmp_path / "b")),
                "c": LocalVolume(slug="c", path=str(tmp_path / "c")),
            },
            sync_endpoints={
                "ep-a": SyncEndpoint(slug="ep-a", volume="a"),
                "ep-b": SyncEndpoint(slug="ep-b", volume="b"),
                "ep-c": SyncEndpoint(slug="ep-c", volume="c"),
            },
            syncs={
                "s1": SyncConfig(slug="s1", source="ep-a", destination="ep-b"),
                "s2": SyncConfig(slug="s2", source="ep-b", destination="ep-c"),
            },
        )
        started: list[str] = []
        check_all_syncs(config, on_check_start=started.append, only_syncs=only_syncs)
        assert check_step_count(config, only_syncs) == len(started)


class TestStandaloneEndpointProbing:
    """Tests for standalone SSH endpoint probing (bastions, orphans) in Phase 1."""

    def _make_bastion_config(self, tmp_path: Path) -> Config:
        """Config with a remote volume that goes through a bastion."""
        src = tmp_path / "src"
        src.mkdir()
        (src / ".nbkp-vol").touch()
        (src / ".nbkp-src").touch()
        (src / ".nbkp-dst").touch()

        bastion = SshEndpoint(
            slug="bastion",
            host="bastion.example.com",
            user="admin",
        )
        target = SshEndpoint(
            slug="target",
            host="target.internal",
            user="backup",
            proxy_jump="bastion",
        )
        return Config(
            ssh_endpoints={"bastion": bastion, "target": target},
            volumes={
                "local": LocalVolume(slug="local", path=str(src)),
                "remote": RemoteVolume(
                    slug="remote",
                    ssh_endpoint="target",
                    path="/data",
                ),
            },
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="local"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="remote"),
            },
            syncs={
                "s1": SyncConfig(slug="s1", source="ep-src", destination="ep-dst"),
            },
        )

    def _bastion_patches(
        self,
        bastion_diag: SshEndpointDiagnostics = _REACHABLE_SSH_DIAG,
    ):  # type: ignore[no-untyped-def]
        """Stack of patches common to all bastion tests.

        Mocks SSH endpoint observation, volume observation, endpoint
        observations, and bastion-specific functions to avoid real
        SSH/DNS calls.
        """

        def decorator(fn):  # type: ignore[no-untyped-def]
            @wraps(fn)
            def wrapper(self_inner, tmp_path, *args, **kwargs):  # type: ignore[no-untyped-def]
                with ExitStack() as stack:
                    stack.enter_context(
                        patch(
                            "nbkp.preflight.checks.observe_standalone_endpoint",
                            return_value=bastion_diag,
                        )
                    )
                    stack.enter_context(
                        patch(
                            "nbkp.preflight.checks.enrich_from_ssh_config",
                            side_effect=lambda ep, **_kw: ep,
                        )
                    )
                    stack.enter_context(
                        patch(
                            "nbkp.preflight.checks.observe_ssh_endpoint",
                            return_value=_ssh_diag(),
                        )
                    )
                    stack.enter_context(
                        patch(
                            "nbkp.preflight.checks.observe_volume",
                            return_value=VolumeDiagnostics(capabilities=_STUB_CAPS),
                        )
                    )
                    stack.enter_context(
                        patch(
                            "nbkp.preflight.checks.observe_source_endpoint",
                            return_value=SourceEndpointDiagnostics(
                                endpoint_slug="ep-src", sentinel_exists=True
                            ),
                        )
                    )
                    stack.enter_context(
                        patch(
                            "nbkp.preflight.checks.observe_destination_endpoint",
                            return_value=DestinationEndpointDiagnostics(
                                endpoint_slug="ep-dst",
                                sentinel_exists=True,
                                endpoint_writable=True,
                            ),
                        )
                    )
                    fn(self_inner, tmp_path, *args, **kwargs)

            return wrapper

        return decorator

    def test_bastion_appears_in_ssh_statuses(self, tmp_path: Path) -> None:
        """Bastion endpoints appear in ssh_endpoint_statuses after check_all_syncs."""

        @self._bastion_patches()
        def _run(self_inner: TestStandaloneEndpointProbing, tmp_path: Path) -> None:
            config = self_inner._make_bastion_config(tmp_path)
            result = check_all_syncs(
                config,
                resolved_endpoints={
                    "remote": ResolvedEndpoint(
                        server=config.ssh_endpoints["target"],
                        proxy_chain=[config.ssh_endpoints["bastion"]],
                    ),
                },
            )
            assert "bastion" in result.ssh_endpoint_statuses
            assert result.ssh_endpoint_statuses["bastion"].active is True
            # No tool errors — bastions have default (empty) tool needs
            assert result.ssh_endpoint_statuses["bastion"].errors == []
            # host_tools is None — no tool probing on bastions
            assert (
                result.ssh_endpoint_statuses["bastion"].diagnostics.host_tools is None
            )

        _run(self, tmp_path)

    def test_unreachable_bastion(self, tmp_path: Path) -> None:
        """Unreachable bastion gets UNREACHABLE error."""

        @self._bastion_patches(bastion_diag=SshEndpointDiagnostics(ssh_reachable=False))
        def _run(self_inner: TestStandaloneEndpointProbing, tmp_path: Path) -> None:
            config = self_inner._make_bastion_config(tmp_path)
            result = check_all_syncs(
                config,
                resolved_endpoints={
                    "remote": ResolvedEndpoint(
                        server=config.ssh_endpoints["target"],
                        proxy_chain=[config.ssh_endpoints["bastion"]],
                    ),
                },
            )
            assert "bastion" in result.ssh_endpoint_statuses
            bastion_status = result.ssh_endpoint_statuses["bastion"]
            assert bastion_status.active is False
            assert SshEndpointError.UNREACHABLE in bastion_status.errors

        _run(self, tmp_path)

    def test_multi_hop_bastion_chain(self, tmp_path: Path) -> None:
        """All hops in a multi-hop bastion chain appear in statuses."""

        @self._bastion_patches()
        def _run(self_inner: TestStandaloneEndpointProbing, tmp_path: Path) -> None:
            src = tmp_path / "src"
            src.mkdir(exist_ok=True)
            (src / ".nbkp-vol").touch()
            (src / ".nbkp-src").touch()
            (src / ".nbkp-dst").touch()

            bastion1 = SshEndpoint(
                slug="bastion1", host="bastion1.example.com", user="admin"
            )
            bastion2 = SshEndpoint(
                slug="bastion2",
                host="bastion2.internal",
                user="admin",
                proxy_jump="bastion1",
            )
            target = SshEndpoint(
                slug="target",
                host="target.internal",
                user="backup",
                proxy_jump="bastion2",
            )
            config = Config(
                ssh_endpoints={
                    "bastion1": bastion1,
                    "bastion2": bastion2,
                    "target": target,
                },
                volumes={
                    "local": LocalVolume(slug="local", path=str(src)),
                    "remote": RemoteVolume(
                        slug="remote", ssh_endpoint="target", path="/data"
                    ),
                },
                sync_endpoints={
                    "ep-src": SyncEndpoint(slug="ep-src", volume="local"),
                    "ep-dst": SyncEndpoint(slug="ep-dst", volume="remote"),
                },
                syncs={
                    "s1": SyncConfig(slug="s1", source="ep-src", destination="ep-dst"),
                },
            )

            result = check_all_syncs(
                config,
                resolved_endpoints={
                    "remote": ResolvedEndpoint(
                        server=target,
                        proxy_chain=[bastion1, bastion2],
                    ),
                },
            )
            assert "bastion1" in result.ssh_endpoint_statuses
            assert "bastion2" in result.ssh_endpoint_statuses
            assert result.ssh_endpoint_statuses["bastion1"].active is True
            assert result.ssh_endpoint_statuses["bastion2"].active is True

        _run(self, tmp_path)

    @patch(
        "nbkp.preflight.checks.observe_ssh_endpoint",
        return_value=_ssh_diag(),
    )
    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    def test_no_bastions_when_no_proxy_jumps(
        self,
        _mock_caps: MagicMock,
        _mock_ssh: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Config without proxy jumps has no bastion statuses."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        (src / ".nbkp-vol").touch()
        (dst / ".nbkp-vol").touch()
        (src / ".nbkp-src").touch()
        (dst / ".nbkp-dst").touch()

        config = Config(
            volumes={
                "src": LocalVolume(slug="src", path=str(src)),
                "dst": LocalVolume(slug="dst", path=str(dst)),
            },
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={
                "s1": SyncConfig(slug="s1", source="ep-src", destination="ep-dst"),
            },
        )
        result = check_all_syncs(config)
        # Only "localhost" — no standalone endpoints
        assert set(result.ssh_endpoint_statuses.keys()) == {"localhost"}

    def test_orphan_endpoint_probed(self, tmp_path: Path) -> None:
        """Orphan endpoints (defined but not referenced by volumes) are probed."""

        @self._bastion_patches()
        def _run(self_inner: TestStandaloneEndpointProbing, tmp_path: Path) -> None:
            src = tmp_path / "src"
            src.mkdir(exist_ok=True)
            (src / ".nbkp-vol").touch()
            (src / ".nbkp-src").touch()
            (src / ".nbkp-dst").touch()

            orphan = SshEndpoint(slug="orphan", host="orphan.example.com", user="admin")
            target = SshEndpoint(slug="target", host="target.internal", user="backup")
            config = Config(
                ssh_endpoints={"orphan": orphan, "target": target},
                volumes={
                    "local": LocalVolume(slug="local", path=str(src)),
                    "remote": RemoteVolume(
                        slug="remote", ssh_endpoint="target", path="/data"
                    ),
                },
                sync_endpoints={
                    "ep-src": SyncEndpoint(slug="ep-src", volume="local"),
                    "ep-dst": SyncEndpoint(slug="ep-dst", volume="remote"),
                },
                syncs={
                    "s1": SyncConfig(slug="s1", source="ep-src", destination="ep-dst"),
                },
            )
            result = check_all_syncs(
                config,
                resolved_endpoints={
                    "remote": ResolvedEndpoint(
                        server=target,
                        proxy_chain=[],
                    ),
                },
            )
            assert "orphan" in result.ssh_endpoint_statuses
            assert result.ssh_endpoint_statuses["orphan"].active is True
            assert result.ssh_endpoint_statuses["orphan"].errors == []
            # No tool probing on standalone endpoints
            assert result.ssh_endpoint_statuses["orphan"].diagnostics.host_tools is None

        _run(self, tmp_path)

    def _make_alternate_config(self, tmp_path: Path) -> Config:
        """``remote`` (via ``target``, through ``bastion``) is synced by
        ``s1``; ``other`` (via ``other-host``, or the ``home``-only
        alternate ``other-lan``) by ``s2``."""
        config = self._make_bastion_config(tmp_path)
        return Config(
            ssh_endpoints={
                **config.ssh_endpoints,
                "other-host": SshEndpoint(slug="other-host", host="other.example"),
                "other-lan": SshEndpoint(
                    slug="other-lan", host="other.lan", location="home"
                ),
            },
            volumes={
                **config.volumes,
                "other": RemoteVolume(
                    slug="other",
                    ssh_endpoint="other-host",
                    ssh_endpoints=["other-host", "other-lan"],
                    path="/other",
                ),
            },
            sync_endpoints={
                **config.sync_endpoints,
                "ep-other": SyncEndpoint(slug="ep-other", volume="other"),
            },
            syncs={
                **config.syncs,
                "s2": SyncConfig(slug="s2", source="ep-src", destination="ep-other"),
            },
        )

    def _resolved(self, config: Config) -> ResolvedEndpoints:
        return {
            "remote": ResolvedEndpoint(
                server=config.ssh_endpoints["target"],
                proxy_chain=[config.ssh_endpoints["bastion"]],
            ),
            "other": ResolvedEndpoint(server=config.ssh_endpoints["other-host"]),
        }

    def test_endpoint_at_excluded_location_not_probed(self, tmp_path: Path) -> None:
        @self._bastion_patches()
        def _run(self_inner: TestStandaloneEndpointProbing, tmp_path: Path) -> None:
            config = self_inner._make_alternate_config(tmp_path)
            resolved = self_inner._resolved(config)
            with patch(
                "nbkp.preflight.checks.observe_standalone_endpoint",
                return_value=_REACHABLE_SSH_DIAG,
            ) as standalone:
                result = check_all_syncs(
                    config, resolved_endpoints=resolved, exclude_locations=["home"]
                )
            probed = {call.args[0].slug for call in standalone.call_args_list}
            assert probed == {"bastion"}
            assert "other-lan" not in result.ssh_endpoint_statuses
            assert check_step_count(config, None, resolved, ["home"]) == (
                len(result.ssh_endpoint_statuses)
                + len(result.volume_statuses)
                + len(result.source_endpoint_statuses)
                + len(result.destination_endpoint_statuses)
            )

        _run(self, tmp_path)

    def test_only_syncs_probes_only_endpoints_of_selected_volumes(
        self, tmp_path: Path
    ) -> None:
        @self._bastion_patches()
        def _run(self_inner: TestStandaloneEndpointProbing, tmp_path: Path) -> None:
            config = self_inner._make_alternate_config(tmp_path)
            with patch(
                "nbkp.preflight.checks.observe_standalone_endpoint",
                return_value=_REACHABLE_SSH_DIAG,
            ) as standalone:
                result = check_all_syncs(
                    config,
                    only_syncs=["s1"],
                    resolved_endpoints=self_inner._resolved(config),
                )
            probed = {call.args[0].slug for call in standalone.call_args_list}
            assert probed == {"bastion"}
            assert set(result.ssh_endpoint_statuses) == {
                "localhost",
                "target",
                "bastion",
            }

        _run(self, tmp_path)


def _alternate_endpoint_config(archive: str, photos: str) -> Config:
    """``archive`` is only reachable from home (``nas-lan``); ``photos`` has
    the same primary but also ``nas-wan``, reachable while travelling."""
    return Config(
        ssh_endpoints={
            "nas-lan": SshEndpoint(slug="nas-lan", host="nas.lan", location="home"),
            "nas-wan": SshEndpoint(
                slug="nas-wan", host="nas.example.com", location="travel"
            ),
        },
        volumes={
            "laptop": LocalVolume(slug="laptop", path="/home/me"),
            archive: RemoteVolume(slug=archive, ssh_endpoint="nas-lan", path="/a"),
            photos: RemoteVolume(
                slug=photos,
                ssh_endpoint="nas-lan",
                ssh_endpoints=["nas-lan", "nas-wan"],
                path="/p",
            ),
        },
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="laptop"),
            "ep-archive": SyncEndpoint(slug="ep-archive", volume=archive),
            "ep-photos": SyncEndpoint(slug="ep-photos", volume=photos),
        },
        syncs={
            "to-archive": SyncConfig(
                slug="to-archive", source="ep-src", destination="ep-archive"
            ),
        },
    )


class TestAlternateEndpointUnderLocationExclusion:
    """Volumes sharing a primary endpoint but resolved to different ones."""

    @pytest.mark.parametrize(
        ("archive", "photos"),
        [("a-archive", "b-photos"), ("z-archive", "b-photos")],
    )
    def test_each_volume_is_checked_through_its_resolved_endpoint(
        self, archive: str, photos: str
    ) -> None:
        config = _alternate_endpoint_config(archive, photos)
        resolved = resolve_all_endpoints(
            config,
            EndpointFilter(exclude_locations=["home"]),
            ssh_config=paramiko.SSHConfig(),
            lookup=lambda _host: {"203.0.113.7"},
        )
        probed: list[str] = []
        observed: list[str] = []

        def observe_ssh(vol: Volume, **_kwargs: object) -> SshEndpointDiagnostics:
            probed.append(vol.slug)
            return _ssh_diag(ssh_reachable=True)

        def observe_vol(vol: Volume, **_kwargs: object) -> VolumeDiagnostics:
            observed.append(vol.slug)
            return VolumeDiagnostics(capabilities=_STUB_CAPS)

        with (
            patch(
                "nbkp.preflight.checks.observe_ssh_endpoint", side_effect=observe_ssh
            ),
            patch("nbkp.preflight.checks.observe_volume", side_effect=observe_vol),
            patch(
                "nbkp.preflight.checks.observe_standalone_endpoint",
                return_value=_REACHABLE_SSH_DIAG,
            ),
            patch(
                "nbkp.preflight.checks.enrich_from_ssh_config",
                side_effect=lambda ep, **_kw: ep,
            ),
            patch(
                "nbkp.preflight.checks.observe_source_endpoint",
                return_value=SourceEndpointDiagnostics(
                    endpoint_slug="ep-src", sentinel_exists=True
                ),
            ),
            patch(
                "nbkp.preflight.checks.observe_destination_endpoint",
                return_value=DestinationEndpointDiagnostics(
                    endpoint_slug="ep-archive",
                    sentinel_exists=True,
                    endpoint_writable=True,
                ),
            ),
        ):
            result = check_all_syncs(config, resolved_endpoints=resolved)

        assert archive not in probed and archive not in observed
        assert result.volume_statuses[photos].active is True
        assert result.volume_statuses[photos].ssh_endpoint_status.slug == "nas-wan"
        assert result.ssh_endpoint_statuses["nas-wan"].active is True
        assert result.volume_statuses[archive].active is False
        assert result.ssh_endpoint_statuses["nas-lan"].errors == [
            SshEndpointError.LOCATION_EXCLUDED
        ]
        assert check_step_count(config, None, resolved) == (
            len(result.ssh_endpoint_statuses)
            + len(result.volume_statuses)
            + len(result.source_endpoint_statuses)
            + len(result.destination_endpoint_statuses)
        )


class TestCheckHardLinkDest:
    def _make_hl_config(
        self, tmp_src: Path, tmp_dst: Path
    ) -> tuple[Config, SyncConfig]:
        src_vol = LocalVolume(slug="src", path=str(tmp_src))
        dst_vol = LocalVolume(slug="dst", path=str(tmp_dst))
        sync = SyncConfig(
            slug="s1",
            source="ep-hl-src",
            destination="ep-hl-dst",
        )
        config = Config(
            volumes={"src": src_vol, "dst": dst_vol},
            sync_endpoints={
                "ep-hl-src": SyncEndpoint(
                    slug="ep-hl-src",
                    volume="src",
                    subdir="data",
                ),
                "ep-hl-dst": SyncEndpoint(
                    slug="ep-hl-dst",
                    volume="dst",
                    subdir="backup",
                    hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
                ),
            },
            syncs={"s1": sync},
        )
        return config, sync

    def _setup_active_sentinels(self, src: Path, dst: Path) -> None:
        (src / ".nbkp-vol").touch()
        (dst / ".nbkp-vol").touch()
        (src / "data").mkdir(exist_ok=True)
        (src / "data" / ".nbkp-src").touch()
        (dst / "backup").mkdir(exist_ok=True)
        (dst / "backup" / ".nbkp-dst").touch()

    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    def test_snapshots_dir_not_found(
        self,
        _mock_caps: MagicMock,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_active_sentinels(src, dst)
        # No snapshots dir

        config, sync = self._make_hl_config(src, dst)

        status = check_sync(sync, config)
        assert status.active is False
        assert (
            DestinationEndpointError.SNAPSHOTS_DIR_NOT_FOUND
            in status.destination_endpoint_status.errors
        )

    @patch("nbkp.preflight.volume_checks.check_volume_capabilities")
    def test_no_hardlink_support_fat(
        self,
        mock_caps: MagicMock,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_active_sentinels(src, dst)
        (dst / "backup" / "snapshots").mkdir()

        config, sync = self._make_hl_config(src, dst)

        def caps_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            return _HL_NO_HARDLINK_CAPS if volume.slug == "dst" else _STUB_CAPS

        mock_caps.side_effect = caps_side_effect

        status = check_sync(sync, config)
        assert status.active is False
        assert (
            DestinationEndpointError.VOL_NO_HARDLINK_SUPPORT
            in status.destination_endpoint_status.errors
        )

    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    def test_active_with_ext4(
        self,
        _mock_caps: MagicMock,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_active_sentinels(src, dst)
        (dst / "backup" / "snapshots").mkdir()
        (dst / "backup" / "latest").symlink_to("/dev/null")

        config, sync = self._make_hl_config(src, dst)

        status = check_sync(sync, config)
        assert status.active is True
        assert status.errors == []

    @patch(
        "nbkp.preflight.checks.observe_ssh_endpoint",
        return_value=_ssh_diag(host_tools=_HL_NO_STAT_HOST_TOOLS),
    )
    @patch("nbkp.preflight.volume_checks.check_volume_capabilities")
    def test_stat_not_found(
        self,
        mock_caps: MagicMock,
        _mock_ssh: MagicMock,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_active_sentinels(src, dst)

        config, sync = self._make_hl_config(src, dst)

        def caps_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            return _HL_NO_STAT_CAPS if volume.slug == "dst" else _STUB_CAPS

        mock_caps.side_effect = caps_side_effect

        status = check_sync(sync, config)
        assert status.active is False
        ssh_errors = (
            status.destination_endpoint_status.volume_status.ssh_endpoint_status.errors
        )
        assert SshEndpointError.STAT_NOT_FOUND in ssh_errors
        # No hardlink support check when stat is missing
        assert (
            DestinationEndpointError.VOL_NO_HARDLINK_SUPPORT
            not in status.destination_endpoint_status.errors
        )

    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    def test_no_btrfs_checks_for_hardlink(
        self,
        _mock_caps: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Hard-link mode should not run any btrfs-specific checks."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_active_sentinels(src, dst)
        (dst / "backup" / "snapshots").mkdir()

        config, sync = self._make_hl_config(src, dst)

        status = check_sync(sync, config)
        ssh_errors = (
            status.destination_endpoint_status.volume_status.ssh_endpoint_status.errors
        )
        assert SshEndpointError.BTRFS_NOT_FOUND not in ssh_errors
        assert (
            DestinationEndpointError.VOL_NOT_BTRFS
            not in status.destination_endpoint_status.errors
        )
        assert (
            DestinationEndpointError.STAGING_NOT_BTRFS_SUBVOLUME
            not in status.destination_endpoint_status.errors
        )
        assert (
            DestinationEndpointError.STAGING_SUBVOL_NOT_FOUND
            not in status.destination_endpoint_status.errors
        )

    @patch("nbkp.preflight.checks.observe_ssh_endpoint")
    @patch("nbkp.preflight.volume_checks.check_volume_capabilities")
    @patch("nbkp.remote.dispatch.run_remote_command")
    def test_remote_no_hardlink_support(
        self,
        mock_run: MagicMock,
        mock_caps: MagicMock,
        mock_ssh_ep: MagicMock,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / ".nbkp-vol").touch()
        (src / "data").mkdir()
        (src / "data" / ".nbkp-src").touch()

        dst_server = SshEndpoint(slug="dst-server", host="dst.local")
        src_vol = LocalVolume(slug="src", path=str(src))
        dst_vol = RemoteVolume(
            slug="dst",
            ssh_endpoint="dst-server",
            path="/backup",
        )
        sync = SyncConfig(
            slug="s1",
            source="ep-rhl-src",
            destination="ep-rhl-dst",
        )
        config = Config(
            ssh_endpoints={"dst-server": dst_server},
            volumes={"src": src_vol, "dst": dst_vol},
            sync_endpoints={
                "ep-rhl-src": SyncEndpoint(
                    slug="ep-rhl-src",
                    volume="src",
                    subdir="data",
                ),
                "ep-rhl-dst": SyncEndpoint(
                    slug="ep-rhl-dst",
                    volume="dst",
                    subdir="backup",
                    hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
                ),
            },
            syncs={"s1": sync},
        )

        def ssh_ep_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            if isinstance(volume, RemoteVolume):
                return _ssh_diag(host_tools=_STUB_HOST_TOOLS, ssh_reachable=True)
            return _ssh_diag()

        mock_ssh_ep.side_effect = ssh_ep_side_effect

        def caps_side_effect(volume, *args, **kwargs):  # type: ignore[no-untyped-def]
            return _HL_NO_HARDLINK_CAPS if volume.slug == "dst" else _STUB_CAPS

        mock_caps.side_effect = caps_side_effect

        def remote_side_effect(
            server: SshEndpoint,
            cmd: list[str],
            proxy_chain: list[SshEndpoint] | None = None,
            input: str | None = None,
        ) -> MagicMock:
            if cmd == [
                "test",
                "-f",
                "/backup/backup/.nbkp-dst",
            ]:
                return MagicMock(returncode=0)
            if cmd == [
                "test",
                "-d",
                "/backup/backup/snapshots",
            ]:
                return MagicMock(returncode=0)
            return MagicMock(returncode=0, stdout="/dev/null\n")

        mock_run.side_effect = remote_side_effect

        status = check_sync(sync, config, _make_resolved(config))
        assert status.active is False
        assert (
            DestinationEndpointError.VOL_NO_HARDLINK_SUPPORT
            in status.destination_endpoint_status.errors
        )


class TestCheckSourceLatest:
    """Tests for SRC_EP_LATEST_SYMLINK_NOT_FOUND check."""

    def _make_config(
        self,
        tmp_src: Path,
        tmp_dst: Path,
        source_snapshot: str = "btrfs",
    ) -> tuple[Config, SyncConfig]:
        src_vol = LocalVolume(slug="src", path=str(tmp_src))
        dst_vol = LocalVolume(slug="dst", path=str(tmp_dst))
        src_ep = (
            SyncEndpoint(
                slug="ep-srclatest-src",
                volume="src",
                subdir="data",
                btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
            )
            if source_snapshot == "btrfs"
            else SyncEndpoint(
                slug="ep-srclatest-src",
                volume="src",
                subdir="data",
                hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
            )
        )
        sync = SyncConfig(
            slug="s1",
            source="ep-srclatest-src",
            destination="ep-srclatest-dst",
        )
        config = Config(
            volumes={"src": src_vol, "dst": dst_vol},
            sync_endpoints={
                "ep-srclatest-src": src_ep,
                "ep-srclatest-dst": SyncEndpoint(
                    slug="ep-srclatest-dst",
                    volume="dst",
                    subdir="backup",
                ),
            },
            syncs={"s1": sync},
        )
        return config, sync

    def _setup_sentinels(self, src: Path, dst: Path) -> None:
        (src / ".nbkp-vol").touch()
        (dst / ".nbkp-vol").touch()
        (src / "data").mkdir(exist_ok=True)
        (src / "data" / ".nbkp-src").touch()
        (dst / "backup").mkdir(exist_ok=True)
        (dst / "backup" / ".nbkp-dst").touch()

    @patch(
        "nbkp.preflight.checks.observe_ssh_endpoint",
        return_value=_ssh_diag(host_tools=_BTRFS_FULL_HOST_TOOLS),
    )
    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    def test_btrfs_source_latest_missing(
        self, _mock_caps: MagicMock, _mock_ssh: MagicMock, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_sentinels(src, dst)
        # No latest/ under source

        config, sync = self._make_config(src, dst, "btrfs")

        status = check_sync(sync, config)
        assert (
            SourceEndpointError.LATEST_SYMLINK_NOT_FOUND
            in status.source_endpoint_status.errors
        )

    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    def test_hard_link_source_latest_missing(
        self, _mock_caps: MagicMock, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_sentinels(src, dst)
        # No latest/ under source

        config, sync = self._make_config(src, dst, "hard-link")

        status = check_sync(sync, config)
        assert (
            SourceEndpointError.LATEST_SYMLINK_NOT_FOUND
            in status.source_endpoint_status.errors
        )

    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    def test_btrfs_source_latest_present(
        self, _mock_caps: MagicMock, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_sentinels(src, dst)

        _ts = create_snapshot_timestamp(
            datetime(2026, 1, 1, tzinfo=UTC),
            LocalVolume(slug="x", path="/x"),
            "linux",
        )
        (src / "data" / "snapshots").mkdir(exist_ok=True)
        snap = src / "data" / "snapshots" / _ts.name
        snap.mkdir()
        (src / "data" / "latest").symlink_to(f"snapshots/{_ts.name}")

        config, sync = self._make_config(src, dst, "btrfs")

        status = check_sync(sync, config)
        assert (
            SourceEndpointError.LATEST_SYMLINK_NOT_FOUND
            not in status.source_endpoint_status.errors
        )
        assert (
            SourceEndpointError.LATEST_SYMLINK_INVALID
            not in status.source_endpoint_status.errors
        )

    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    def test_hard_link_source_latest_symlink(
        self, _mock_caps: MagicMock, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_sentinels(src, dst)

        _ts = create_snapshot_timestamp(
            datetime(2024, 1, 1, tzinfo=UTC),
            LocalVolume(slug="x", path="/x"),
            "linux",
        )
        snap = src / "data" / "snapshots" / _ts.name
        snap.mkdir(parents=True)
        (src / "data" / "latest").symlink_to(f"snapshots/{_ts.name}")

        config, sync = self._make_config(src, dst, "hard-link")

        status = check_sync(sync, config)
        assert (
            SourceEndpointError.LATEST_SYMLINK_NOT_FOUND
            not in status.source_endpoint_status.errors
        )

    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    def test_no_snapshots_source_skips_check(
        self, _mock_caps: MagicMock, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_sentinels(src, dst)
        # No latest/ but source has no snapshots

        src_vol = LocalVolume(slug="src", path=str(src))
        dst_vol = LocalVolume(slug="dst", path=str(dst))
        sync = SyncConfig(
            slug="s1",
            source="ep-nsnap-src",
            destination="ep-nsnap-dst",
        )
        config = Config(
            volumes={"src": src_vol, "dst": dst_vol},
            sync_endpoints={
                "ep-nsnap-src": SyncEndpoint(
                    slug="ep-nsnap-src",
                    volume="src",
                    subdir="data",
                ),
                "ep-nsnap-dst": SyncEndpoint(
                    slug="ep-nsnap-dst",
                    volume="dst",
                    subdir="backup",
                ),
            },
            syncs={"s1": sync},
        )

        status = check_sync(sync, config)
        assert (
            SourceEndpointError.LATEST_SYMLINK_NOT_FOUND
            not in status.source_endpoint_status.errors
        )


class TestCheckSourceSnapshots:
    """Tests for SRC_EP_SNAPSHOTS_DIR_NOT_FOUND check."""

    def _make_config(
        self,
        tmp_src: Path,
        tmp_dst: Path,
        source_snapshot: str = "btrfs",
    ) -> tuple[Config, SyncConfig]:
        src_vol = LocalVolume(slug="src", path=str(tmp_src))
        dst_vol = LocalVolume(slug="dst", path=str(tmp_dst))
        src_ep = (
            SyncEndpoint(
                slug="ep-srcsnap-src",
                volume="src",
                subdir="data",
                btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
            )
            if source_snapshot == "btrfs"
            else SyncEndpoint(
                slug="ep-srcsnap-src",
                volume="src",
                subdir="data",
                hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
            )
        )
        sync = SyncConfig(
            slug="s1",
            source="ep-srcsnap-src",
            destination="ep-srcsnap-dst",
        )
        config = Config(
            volumes={"src": src_vol, "dst": dst_vol},
            sync_endpoints={
                "ep-srcsnap-src": src_ep,
                "ep-srcsnap-dst": SyncEndpoint(
                    slug="ep-srcsnap-dst",
                    volume="dst",
                    subdir="backup",
                ),
            },
            syncs={"s1": sync},
        )
        return config, sync

    def _setup_sentinels(self, src: Path, dst: Path) -> None:
        (src / ".nbkp-vol").touch()
        (dst / ".nbkp-vol").touch()
        (src / "data").mkdir(exist_ok=True)
        (src / "data" / ".nbkp-src").touch()
        (dst / "backup").mkdir(exist_ok=True)
        (dst / "backup" / ".nbkp-dst").touch()

    @patch(
        "nbkp.preflight.checks.observe_ssh_endpoint",
        return_value=_ssh_diag(host_tools=_BTRFS_FULL_HOST_TOOLS),
    )
    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    def test_btrfs_source_snapshots_missing(
        self, _mock_caps: MagicMock, _mock_ssh: MagicMock, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_sentinels(src, dst)
        (src / "data" / "latest").symlink_to("/dev/null")
        # No snapshots/ dir

        config, sync = self._make_config(src, dst, "btrfs")

        status = check_sync(sync, config)
        assert (
            SourceEndpointError.SNAPSHOTS_DIR_NOT_FOUND
            in status.source_endpoint_status.errors
        )

    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    def test_hard_link_source_snapshots_missing(
        self, _mock_caps: MagicMock, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_sentinels(src, dst)
        (src / "data" / "latest").symlink_to("/dev/null")
        # No snapshots/ dir

        config, sync = self._make_config(src, dst, "hard-link")

        status = check_sync(sync, config)
        assert (
            SourceEndpointError.SNAPSHOTS_DIR_NOT_FOUND
            in status.source_endpoint_status.errors
        )

    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    def test_btrfs_source_snapshots_present(
        self, _mock_caps: MagicMock, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_sentinels(src, dst)
        (src / "data" / "snapshots").mkdir()
        (src / "data" / "latest").symlink_to("/dev/null")

        config, sync = self._make_config(src, dst, "btrfs")

        status = check_sync(sync, config)
        assert (
            SourceEndpointError.SNAPSHOTS_DIR_NOT_FOUND
            not in status.source_endpoint_status.errors
        )

    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    def test_no_snapshots_source_skips_check(
        self, _mock_caps: MagicMock, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_sentinels(src, dst)
        # No snapshots/ but source has no snapshots enabled

        src_vol = LocalVolume(slug="src", path=str(src))
        dst_vol = LocalVolume(slug="dst", path=str(dst))
        sync = SyncConfig(
            slug="s1",
            source="ep-nsnap2-src",
            destination="ep-nsnap2-dst",
        )
        config = Config(
            volumes={"src": src_vol, "dst": dst_vol},
            sync_endpoints={
                "ep-nsnap2-src": SyncEndpoint(
                    slug="ep-nsnap2-src",
                    volume="src",
                    subdir="data",
                ),
                "ep-nsnap2-dst": SyncEndpoint(
                    slug="ep-nsnap2-dst",
                    volume="dst",
                    subdir="backup",
                ),
            },
            syncs={"s1": sync},
        )

        status = check_sync(sync, config)
        assert (
            SourceEndpointError.SNAPSHOTS_DIR_NOT_FOUND
            not in status.source_endpoint_status.errors
        )


class TestCheckDevnullLatest:
    """Tests for /dev/null latest symlink handling."""

    def _make_config(
        self,
        tmp_src: Path,
        tmp_dst: Path,
        snapshot_mode: str = "hard-link",
    ) -> tuple[Config, SyncConfig]:
        src_vol = LocalVolume(slug="src", path=str(tmp_src))
        dst_vol = LocalVolume(slug="dst", path=str(tmp_dst))
        dst_ep = (
            SyncEndpoint(
                slug="ep-devnull-dst",
                volume="dst",
                subdir="backup",
                hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
            )
            if snapshot_mode == "hard-link"
            else SyncEndpoint(
                slug="ep-devnull-dst",
                volume="dst",
                subdir="backup",
                btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
            )
        )
        sync = SyncConfig(
            slug="s1",
            source="ep-devnull-src",
            destination="ep-devnull-dst",
        )
        config = Config(
            volumes={"src": src_vol, "dst": dst_vol},
            sync_endpoints={
                "ep-devnull-src": SyncEndpoint(
                    slug="ep-devnull-src",
                    volume="src",
                    subdir="data",
                    hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
                ),
                "ep-devnull-dst": dst_ep,
            },
            syncs={"s1": sync},
        )
        return config, sync

    def _setup_sentinels(self, src: Path, dst: Path) -> None:
        (src / ".nbkp-vol").touch()
        (dst / ".nbkp-vol").touch()
        (src / "data").mkdir(exist_ok=True)
        (src / "data" / ".nbkp-src").touch()
        (dst / "backup").mkdir(exist_ok=True)
        (dst / "backup" / ".nbkp-dst").touch()

    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    def test_source_devnull_accepted_with_upstream(
        self,
        _mock_caps: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Source latest → /dev/null is valid when an upstream
        sync writes to this source endpoint."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        upstream_src = tmp_path / "upstream"
        src.mkdir()
        dst.mkdir()
        upstream_src.mkdir()
        self._setup_sentinels(src, dst)
        (src / "data" / "snapshots").mkdir()
        (src / "data" / "latest").symlink_to("/dev/null")
        (dst / "backup" / "snapshots").mkdir()
        (dst / "backup" / "latest").symlink_to("/dev/null")

        src_vol = LocalVolume(slug="src", path=str(src))
        dst_vol = LocalVolume(slug="dst", path=str(dst))
        upstream_vol = LocalVolume(slug="upstream", path=str(upstream_src))
        sync = SyncConfig(
            slug="s1",
            source="ep-up-src",
            destination="ep-up-dst",
        )
        upstream = SyncConfig(
            slug="upstream",
            source="ep-up-usrc",
            destination="ep-up-src",
        )
        config = Config(
            volumes={
                "src": src_vol,
                "dst": dst_vol,
                "upstream": upstream_vol,
            },
            sync_endpoints={
                "ep-up-src": SyncEndpoint(
                    slug="ep-up-src",
                    volume="src",
                    subdir="data",
                    hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
                ),
                "ep-up-dst": SyncEndpoint(
                    slug="ep-up-dst",
                    volume="dst",
                    subdir="backup",
                    hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
                ),
                "ep-up-usrc": SyncEndpoint(
                    slug="ep-up-usrc",
                    volume="upstream",
                ),
            },
            syncs={"s1": sync, "upstream": upstream},
        )
        status = check_sync(sync, config)
        assert (
            SourceEndpointError.LATEST_SYMLINK_INVALID
            not in status.source_endpoint_status.errors
        )
        assert (
            SourceEndpointError.LATEST_SYMLINK_NOT_FOUND
            not in status.source_endpoint_status.errors
        )

    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    def test_source_devnull_inactive_in_dry_run_with_upstream(
        self,
        _mock_caps: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Source latest → /dev/null with upstream sync is marked
        inactive in dry-run mode because the upstream won't create
        a real snapshot."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        upstream_src = tmp_path / "upstream"
        src.mkdir()
        dst.mkdir()
        upstream_src.mkdir()
        self._setup_sentinels(src, dst)
        (src / "data" / "snapshots").mkdir()
        (src / "data" / "latest").symlink_to("/dev/null")
        (dst / "backup" / "snapshots").mkdir()
        (dst / "backup" / "latest").symlink_to("/dev/null")

        src_vol = LocalVolume(slug="src", path=str(src))
        dst_vol = LocalVolume(slug="dst", path=str(dst))
        upstream_vol = LocalVolume(slug="upstream", path=str(upstream_src))
        sync = SyncConfig(
            slug="s1",
            source="ep-up-src",
            destination="ep-up-dst",
        )
        upstream = SyncConfig(
            slug="upstream",
            source="ep-up-usrc",
            destination="ep-up-src",
        )
        config = Config(
            volumes={
                "src": src_vol,
                "dst": dst_vol,
                "upstream": upstream_vol,
            },
            sync_endpoints={
                "ep-up-src": SyncEndpoint(
                    slug="ep-up-src",
                    volume="src",
                    subdir="data",
                    hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
                ),
                "ep-up-dst": SyncEndpoint(
                    slug="ep-up-dst",
                    volume="dst",
                    subdir="backup",
                    hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
                ),
                "ep-up-usrc": SyncEndpoint(
                    slug="ep-up-usrc",
                    volume="upstream",
                ),
            },
            syncs={"s1": sync, "upstream": upstream},
        )
        status = check_sync(sync, config, dry_run=True)
        assert SyncError.DRY_RUN_SRC_EP_SNAPSHOT_PENDING in status.errors
        assert not status.active

    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    def test_source_devnull_rejected_without_upstream(
        self,
        _mock_caps: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Source latest → /dev/null is invalid when no upstream
        sync writes to this source endpoint."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_sentinels(src, dst)
        (src / "data" / "snapshots").mkdir()
        (src / "data" / "latest").symlink_to("/dev/null")
        (dst / "backup" / "snapshots").mkdir()
        (dst / "backup" / "latest").symlink_to("/dev/null")

        config, sync = self._make_config(src, dst)

        status = check_sync(sync, config)
        assert SyncError.SRC_EP_LATEST_DEVNULL_NO_UPSTREAM in status.errors

    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    def test_source_dangling_symlink_invalid(
        self,
        _mock_caps: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Source latest pointing to non-existent snapshot is
        invalid."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_sentinels(src, dst)

        _ts = create_snapshot_timestamp(
            datetime(2099, 1, 1, tzinfo=UTC),
            LocalVolume(slug="x", path="/x"),
            "linux",
        )
        (src / "data" / "snapshots").mkdir()
        (src / "data" / "latest").symlink_to(f"snapshots/{_ts.name}")
        (dst / "backup" / "snapshots").mkdir()
        (dst / "backup" / "latest").symlink_to("/dev/null")

        config, sync = self._make_config(src, dst)

        status = check_sync(sync, config)
        assert (
            SourceEndpointError.LATEST_SYMLINK_INVALID
            in status.source_endpoint_status.errors
        )

    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    def test_dest_devnull_accepted(
        self,
        _mock_caps: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Destination latest → /dev/null is valid (no snapshot
        yet)."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_sentinels(src, dst)
        (dst / "backup" / "snapshots").mkdir()
        (dst / "backup" / "latest").symlink_to("/dev/null")

        config, sync = self._make_config(src, dst)

        status = check_sync(sync, config)
        assert (
            DestinationEndpointError.LATEST_SYMLINK_NOT_FOUND
            not in status.destination_endpoint_status.errors
        )
        assert (
            DestinationEndpointError.LATEST_SYMLINK_INVALID
            not in status.destination_endpoint_status.errors
        )

    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    def test_dest_latest_missing(
        self,
        _mock_caps: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Missing destination latest symlink is detected."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_sentinels(src, dst)
        (dst / "backup" / "snapshots").mkdir()
        # No latest symlink

        config, sync = self._make_config(src, dst)

        status = check_sync(sync, config)
        assert (
            DestinationEndpointError.LATEST_SYMLINK_NOT_FOUND
            in status.destination_endpoint_status.errors
        )

    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    def test_dest_dangling_symlink_invalid(
        self,
        _mock_caps: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Destination latest pointing to non-existent snapshot is
        invalid."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_sentinels(src, dst)

        _ts = create_snapshot_timestamp(
            datetime(2099, 1, 1, tzinfo=UTC),
            LocalVolume(slug="x", path="/x"),
            "linux",
        )
        (dst / "backup" / "snapshots").mkdir()
        (dst / "backup" / "latest").symlink_to(f"snapshots/{_ts.name}")

        config, sync = self._make_config(src, dst)

        status = check_sync(sync, config)
        assert (
            DestinationEndpointError.LATEST_SYMLINK_INVALID
            in status.destination_endpoint_status.errors
        )


class TestCheckRemoteVolumeSpaces:
    @patch(
        "nbkp.preflight.checks.observe_ssh_endpoint",
        return_value=_ssh_diag(host_tools=_STUB_HOST_TOOLS, ssh_reachable=True),
    )
    @patch(
        "nbkp.preflight.volume_checks.check_volume_capabilities",
        return_value=_STUB_CAPS,
    )
    @patch("nbkp.remote.dispatch.run_remote_command")
    def test_active(
        self, mock_run: MagicMock, mock_caps: MagicMock, _mock_ssh: MagicMock
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        vol, config = _remote_config(path="/my backup")
        resolved = _make_resolved(config)
        status = check_volume(vol, resolved)
        assert status.active is True
        assert status.diagnostics.capabilities is not None
        mock_run.assert_called_once_with(
            config.ssh_endpoints["nas-server"],
            ["test", "-f", "/my backup/.nbkp-vol"],
            [],
            input=None,
        )


class TestParseRsyncVersion:
    def test_gnu_rsync_3(self) -> None:
        output = (
            "rsync  version 3.2.7  protocol version 31\n"
            "Copyright (C) 1996-2022 by Andrew Tridgell\n"
        )
        assert parse_rsync_version(output) == (3, 2, 7)

    def test_gnu_rsync_3_0_0(self) -> None:
        output = "rsync  version 3.0.0  protocol version 30\n"
        assert parse_rsync_version(output) == (3, 0, 0)

    def test_gnu_rsync_2(self) -> None:
        output = "rsync  version 2.6.9  protocol version 29\n"
        assert parse_rsync_version(output) == (2, 6, 9)

    def test_openrsync(self) -> None:
        output = "openrsync: protocol version 29\nrsync version 2.6.9 compatible\n"
        assert parse_rsync_version(output) == (0, 0, 0)

    def test_empty(self) -> None:
        assert parse_rsync_version("") == (0, 0, 0)

    def test_garbage(self) -> None:
        assert parse_rsync_version("not rsync\n") == (0, 0, 0)


def _rsync_answers(stdout: str, returncode: int = 0) -> FakeCommandRunner:
    return FakeCommandRunner().on(
        "rsync", "--version", returns=completed(returncode, stdout)
    )


class TestCheckRsyncVersionLocal:
    def test_new_enough(self) -> None:
        run = _rsync_answers("rsync  version 3.2.7  protocol version 31\n")
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert check_rsync_version(vol, {}, run=run) is True
        assert run.commands == [["rsync", "--version"]]

    def test_too_old(self) -> None:
        run = _rsync_answers("rsync  version 2.6.9  protocol version 29\n")
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert check_rsync_version(vol, {}, run=run) is False

    def test_openrsync(self) -> None:
        run = _rsync_answers(
            "openrsync: protocol version 29\nrsync version 2.6.9 compatible\n"
        )
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert check_rsync_version(vol, {}, run=run) is False

    def test_command_failure(self) -> None:
        run = _rsync_answers("", returncode=1)
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert check_rsync_version(vol, {}, run=run) is False

    def test_exactly_3_0_0(self) -> None:
        run = _rsync_answers("rsync  version 3.0.0  protocol version 30\n")
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert check_rsync_version(vol, {}, run=run) is True


class TestCheckRsyncVersionRemote:
    def test_new_enough(self) -> None:
        run = _rsync_answers("rsync  version 3.2.7  protocol version 31\n")
        vol, config = _remote_config()
        resolved = _make_resolved(config)
        assert check_rsync_version(vol, resolved, run=run) is True
        assert run.calls[0].endpoint == resolved["nas"]

    def test_too_old(self) -> None:
        run = _rsync_answers("rsync  version 2.6.9  protocol version 29\n")
        vol, config = _remote_config()
        resolved = _make_resolved(config)
        assert check_rsync_version(vol, resolved, run=run) is False


def _nas_and_local_config() -> Config:
    """``remote``: NAS → local ``a``; ``local``: local ``b`` → local ``c``."""
    return Config(
        ssh_endpoints={"nas": SshEndpoint(slug="nas", host="nas.example")},
        volumes={
            "nas": RemoteVolume(slug="nas", ssh_endpoint="nas", path="/data"),
            "a": LocalVolume(slug="a", path="/mnt/a"),
            "b": LocalVolume(slug="b", path="/mnt/b"),
            "c": LocalVolume(slug="c", path="/mnt/c"),
        },
        sync_endpoints={
            "ep-nas": SyncEndpoint(slug="ep-nas", volume="nas"),
            "ep-a": SyncEndpoint(slug="ep-a", volume="a"),
            "ep-b": SyncEndpoint(slug="ep-b", volume="b"),
            "ep-c": SyncEndpoint(slug="ep-c", volume="c"),
        },
        syncs={
            "remote": SyncConfig(slug="remote", source="ep-nas", destination="ep-a"),
            "local": SyncConfig(slug="local", source="ep-b", destination="ep-c"),
        },
    )


def _reachable(*_args: object, **_kwargs: object) -> SshEndpointDiagnostics:
    return _ssh_diag(ssh_reachable=True)


def _stub_volume(*_args: object, **_kwargs: object) -> VolumeDiagnostics:
    return VolumeDiagnostics(capabilities=_STUB_CAPS)


def _src_ok(
    ep: SyncEndpoint, *_args: object, **_kwargs: object
) -> SourceEndpointDiagnostics:
    return SourceEndpointDiagnostics(endpoint_slug=ep.slug, sentinel_exists=True)


def _dst_ok(
    ep: SyncEndpoint, *_args: object, **_kwargs: object
) -> DestinationEndpointDiagnostics:
    return DestinationEndpointDiagnostics(
        endpoint_slug=ep.slug, sentinel_exists=True, endpoint_writable=True
    )


class TestConnectionLostDuringPreflight:
    """A host that drops after the first probe is unreachable for that host only.

    Every remote command opens its own connection, so the NAS can answer the
    host-tool probes and then go away (asleep, Wi-Fi dropped).
    """

    @patch("nbkp.preflight.checks.observe_destination_endpoint", side_effect=_dst_ok)
    @patch("nbkp.preflight.checks.observe_source_endpoint", side_effect=_src_ok)
    @patch("nbkp.preflight.checks.observe_volume")
    @patch("nbkp.preflight.checks.observe_ssh_endpoint", side_effect=_reachable)
    def test_during_volume_observation(
        self, _ssh: MagicMock, mock_volume: MagicMock, *_mocks: MagicMock
    ) -> None:
        def observe(volume: Volume, **_kwargs: object) -> VolumeDiagnostics:
            match volume:
                case RemoteVolume():
                    raise EOFError("connection closed by peer")
                case _:
                    return _stub_volume()

        mock_volume.side_effect = observe
        config = _nas_and_local_config()

        result = check_all_syncs(config, resolved_endpoints=_make_resolved(config))

        nas = result.volume_statuses["nas"]
        assert nas.errors == [VolumeError.SSH_ENDPOINT_INACTIVE]
        assert nas.ssh_endpoint_status.errors == [SshEndpointError.UNREACHABLE]
        assert nas.ssh_endpoint_status.diagnostics.ssh_error == (
            "connection closed by peer"
        )
        assert result.sync_statuses["remote"].is_expected_inactive()
        assert result.sync_statuses["local"].active

    @patch("nbkp.preflight.checks.observe_destination_endpoint", side_effect=_dst_ok)
    @patch("nbkp.preflight.checks.observe_source_endpoint")
    @patch("nbkp.preflight.checks.observe_volume", side_effect=_stub_volume)
    @patch("nbkp.preflight.checks.observe_ssh_endpoint", side_effect=_reachable)
    def test_during_endpoint_observation(
        self, _ssh: MagicMock, _vol: MagicMock, mock_src: MagicMock, _dst: MagicMock
    ) -> None:
        def observe(
            ep: SyncEndpoint, volume: Volume, *_args: object, **_kwargs: object
        ) -> SourceEndpointDiagnostics:
            match volume:
                case RemoteVolume():
                    raise paramiko.SSHException("Error reading SSH protocol banner")
                case _:
                    return _src_ok(ep)

        mock_src.side_effect = observe
        config = _nas_and_local_config()

        result = check_all_syncs(config, resolved_endpoints=_make_resolved(config))

        src = result.source_endpoint_statuses["ep-nas"]
        assert src.errors == [SourceEndpointError.VOLUME_INACTIVE]
        assert src.volume_status.ssh_endpoint_status.errors == [
            SshEndpointError.UNREACHABLE
        ]
        assert result.sync_statuses["remote"].is_expected_inactive()
        assert result.sync_statuses["local"].active

    @patch("nbkp.preflight.checks.observe_volume", side_effect=OSError("disk gone"))
    @patch("nbkp.preflight.checks.observe_ssh_endpoint", side_effect=_reachable)
    def test_local_errors_still_propagate(self, *_mocks: MagicMock) -> None:
        config = _nas_and_local_config()
        with pytest.raises(OSError, match="disk gone"):
            check_all_syncs(config, resolved_endpoints=_make_resolved(config))

    @patch(
        "nbkp.preflight.volume_checks._probe_mount_tools",
        side_effect=EOFError("connection closed by peer"),
    )
    @patch(
        "nbkp.preflight.volume_checks._probe_host_tools",
        return_value=_STUB_HOST_TOOLS,
    )
    def test_during_mount_tool_probe(self, *_mocks: MagicMock) -> None:
        config = _nas_and_local_config()

        diag = observe_ssh_endpoint(
            config.volumes["nas"], _make_resolved(config), probe_mount_tools=True
        )

        assert diag.ssh_reachable is False
        assert diag.ssh_error == "connection closed by peer"
