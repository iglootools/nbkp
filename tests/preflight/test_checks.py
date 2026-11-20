from collections.abc import Collection
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
    DestinationEndpointErrorKind,
    DestinationEndpointStatus,
    SourceEndpointErrorKind,
    SourceEndpointStatus,
)
from nbkp.preflight.status.result import PreflightResult
from nbkp.preflight.status.ssh import (
    HostToolCapabilities,
    SshEndpointDiagnostics,
    SshEndpointErrorKind,
    SshEndpointStatus,
)
from nbkp.preflight.status.sync import SyncErrorKind, SyncStatus
from nbkp.preflight.status.volume import (
    VolumeCapabilities,
    VolumeDiagnostics,
    VolumeErrorKind,
    VolumeStatus,
)
from nbkp.preflight.testkit import check_sync, check_volume
from nbkp.preflight.volume_checks import check_volume_capabilities, observe_ssh_endpoint
from nbkp.remote.endpoints import EndpointFilter, ResolvedEndpoint, ResolvedEndpoints
from nbkp.remote.resolution import resolve_all_endpoints, resolve_proxy_chain
from nbkp.snapshots.common import create_snapshot_timestamp
from tests.fakes import FakeCommandRunner, completed
from tests.preflight.fake_hosts import FAILS, healthy_host, local_tools

_ACTIVE_LOCALHOST_SSH = SshEndpointStatus(
    slug="localhost",
    diagnostics=SshEndpointDiagnostics(
        host_tools=HostToolCapabilities(
            has_rsync=True,
            rsync_version_ok=True,
            has_btrfs=False,
            has_stat=True,
            has_findmnt=True,
        )
    ),
    errors=[],
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
            errors=[VolumeErrorKind.SENTINEL_NOT_FOUND],
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
            errors=[SyncErrorKind.DISABLED],
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
    def test_active(self, tmp_path: Path) -> None:
        vol = LocalVolume(slug="data", path=str(tmp_path))
        (tmp_path / ".nbkp-vol").touch()
        run = healthy_host()
        status = check_volume(vol, run=run, which=local_tools())
        assert status.active is True
        assert status.errors == []
        assert status.diagnostics is not None
        assert status.diagnostics.capabilities is not None
        assert status.diagnostics.capabilities.sentinel_exists is True
        # The capabilities were probed, on the volume itself.
        assert run.commands_starting_with("stat", "-f") == [
            ["stat", "-f", "-c", "%T", str(tmp_path)]
        ]

    def test_inactive(self, tmp_path: Path) -> None:
        vol = LocalVolume(slug="data", path=str(tmp_path))
        run = healthy_host()
        status = check_volume(vol, run=run, which=local_tools())
        assert status.active is False
        assert status.errors == [VolumeErrorKind.SENTINEL_NOT_FOUND]
        # Without its sentinel, a volume's filesystem is not probed.
        assert run.commands_starting_with("stat") == []

    def test_sentinel_directory_is_not_a_sentinel(self, tmp_path: Path) -> None:
        """Like the remote ``test -f``: only a regular file counts."""
        (tmp_path / ".nbkp-vol").mkdir()
        status = check_volume(
            LocalVolume(slug="data", path=str(tmp_path)),
            run=healthy_host(),
            which=local_tools(),
        )
        assert status.errors == [VolumeErrorKind.SENTINEL_NOT_FOUND]

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
    def test_active(self) -> None:
        vol, config = _remote_config()
        resolved = _make_resolved(config)
        run = healthy_host()
        status = check_volume(vol, resolved, run=run)
        assert status.active is True
        assert status.errors == []
        assert status.diagnostics is not None
        assert status.diagnostics.capabilities is not None
        assert status.diagnostics.capabilities.sentinel_exists is True
        assert ["test", "-f", "/backup/.nbkp-vol"] in run.commands
        assert ["stat", "-f", "-c", "%T", "/backup"] in run.commands

    def test_sentinel_missing(self) -> None:
        """SSH succeeds but sentinel file is absent."""
        run = healthy_host(
            FakeCommandRunner().on("test", "-f", "/backup/.nbkp-vol", returns=FAILS)
        )
        vol, config = _remote_config()
        resolved = _make_resolved(config)
        status = check_volume(vol, resolved, run=run)
        assert status.active is False
        assert status.errors == [VolumeErrorKind.SENTINEL_NOT_FOUND]
        assert status.ssh_endpoint_status.diagnostics.ssh_reachable is True
        assert status.diagnostics is not None
        assert status.diagnostics.capabilities is not None
        assert status.diagnostics.capabilities.sentinel_exists is False

    def test_ssh_unreachable(self) -> None:
        """SSH connection fails entirely."""
        run = FakeCommandRunner().on("which", returns=TimeoutError("timed out"))
        vol, config = _remote_config()
        resolved = _make_resolved(config)
        status = check_volume(vol, resolved, run=run)
        assert status.active is False
        assert SshEndpointErrorKind.UNREACHABLE in status.ssh_endpoint_status.errors
        assert status.ssh_endpoint_status.diagnostics.ssh_reachable is False
        assert status.diagnostics is None
        # The first failed probe settles it: nothing else is tried.
        assert run.commands == [["which", "rsync"]]


class TestCheckRemoteVolumeLocationExcluded:
    def test_excluded_volume_no_ssh(self) -> None:
        """Volume excluded by location is marked without SSH."""
        run = FakeCommandRunner()
        vol, _config = _remote_config()
        # Empty resolved_endpoints means volume was excluded
        status = check_volume(vol, {}, run=run)
        assert status.active is False
        assert (
            SshEndpointErrorKind.LOCATION_EXCLUDED in status.ssh_endpoint_status.errors
        )
        assert run.commands == []

    def test_unresolved_endpoint_is_not_contacted(self) -> None:
        """Observed directly, a volume without a resolved endpoint is excluded."""
        run = FakeCommandRunner()
        vol, _config = _remote_config()
        diag = observe_ssh_endpoint(vol, {}, run=run)
        assert diag.location_excluded is True
        assert run.commands == []


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


def _check(
    sync: SyncConfig,
    config: Config,
    *,
    resolved: ResolvedEndpoints | None = None,
    run: FakeCommandRunner | None = None,
    fs_type: str = "ext2/ext3",
    missing_tools: Collection[str] = (),
    dry_run: bool = False,
) -> SyncStatus:
    """Preflight *sync* with every host healthy but for *run*'s own answers,
    and *missing_tools* missing from the orchestrator's ``PATH``."""
    return check_sync(
        sync,
        config,
        resolved,
        dry_run=dry_run,
        run=healthy_host(run, fs_type=fs_type),
        which=local_tools(missing=missing_tools),
    )


@pytest.fixture
def src_dst(tmp_path: Path) -> tuple[Path, Path]:
    """Two empty local volume roots."""
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    return src, dst


def _setup_active_sentinels(src: Path, dst: Path) -> None:
    """Volume sentinels on both roots, endpoint sentinels in ``data`` (source)
    and ``backup`` (destination)."""
    (src / ".nbkp-vol").touch()
    (dst / ".nbkp-vol").touch()
    (src / "data").mkdir(exist_ok=True)
    (src / "data" / ".nbkp-src").touch()
    (dst / "backup").mkdir(exist_ok=True)
    (dst / "backup" / ".nbkp-dst").touch()


def _local_sync(
    src: Path,
    dst: Path,
    *,
    dst_btrfs: bool = False,
    enabled: bool = True,
) -> tuple[Config, SyncConfig]:
    """``s1``: ``src/data`` → ``dst/backup``, btrfs snapshots at the
    destination when *dst_btrfs*."""
    sync = SyncConfig(slug="s1", source="ep-src", destination="ep-dst", enabled=enabled)
    config = Config(
        volumes={
            "src": LocalVolume(slug="src", path=str(src)),
            "dst": LocalVolume(slug="dst", path=str(dst)),
        },
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src", subdir="data"),
            "ep-dst": SyncEndpoint(
                slug="ep-dst",
                volume="dst",
                subdir="backup",
                btrfs_snapshots=BtrfsSnapshotConfig(enabled=dst_btrfs),
            ),
        },
        syncs={"s1": sync},
    )
    return config, sync


class TestCheckSync:
    def test_active_sync(self, src_dst: tuple[Path, Path]) -> None:
        _setup_active_sentinels(*src_dst)
        config, sync = _local_sync(*src_dst)

        status = _check(sync, config)
        assert status.active is True
        assert status.errors == []

    def test_disabled_sync(self, src_dst: tuple[Path, Path]) -> None:
        config, sync = _local_sync(*src_dst, enabled=False)

        status = _check(sync, config)
        assert status.active is False
        assert status.errors == [SyncErrorKind.DISABLED]

    def test_source_unavailable(self, src_dst: tuple[Path, Path]) -> None:
        src, dst = src_dst
        # No .nbkp-vol on src, so it becomes inactive
        (dst / ".nbkp-vol").touch()
        (dst / "backup").mkdir()
        (dst / "backup" / ".nbkp-dst").touch()
        config, sync = _local_sync(src, dst)

        status = _check(sync, config)
        assert status.active is False
        src_vol_errors = status.source_endpoint_status.volume_status.errors
        assert VolumeErrorKind.SENTINEL_NOT_FOUND in src_vol_errors

    def test_missing_src_sentinel(self, src_dst: tuple[Path, Path]) -> None:
        src, dst = src_dst
        _setup_active_sentinels(src, dst)
        (src / "data" / ".nbkp-src").unlink()
        config, sync = _local_sync(src, dst)

        status = _check(sync, config)
        assert status.active is False
        assert (
            SourceEndpointErrorKind.SENTINEL_NOT_FOUND
            in status.source_endpoint_status.errors
        )

    def test_rsync_not_found_on_source(self, src_dst: tuple[Path, Path]) -> None:
        """Rsync missing on a local volume: reported at SSH endpoint level,
        but local volumes are still probed (no SSH_ENDPOINT_INACTIVE cascade)."""
        _setup_active_sentinels(*src_dst)
        config, sync = _local_sync(*src_dst)

        status = _check(sync, config, missing_tools={"rsync"})
        # rsync errors are at the SSH endpoint level (shared by both volumes)
        ssh_errors = (
            status.source_endpoint_status.volume_status.ssh_endpoint_status.errors
        )
        assert SshEndpointErrorKind.RSYNC_NOT_FOUND in ssh_errors
        # Local volumes don't cascade SSH_ENDPOINT_INACTIVE — volume is active
        assert status.source_endpoint_status.volume_status.active is True
        # ...but the sync is inactive, and not expected-inactive: a missing
        # localhost rsync must fail preflight.
        assert SyncErrorKind.ENDPOINT_HOST_ERRORS in status.errors
        assert status.is_expected_inactive() is False

    def test_openrsync_on_the_orchestrator_is_too_old(
        self, src_dst: tuple[Path, Path]
    ) -> None:
        _setup_active_sentinels(*src_dst)
        config, sync = _local_sync(*src_dst)
        run = FakeCommandRunner().on(
            "rsync", "--version", returns=completed(stdout="openrsync: protocol 29\n")
        )

        status = _check(sync, config, run=run)
        ssh_errors = (
            status.source_endpoint_status.volume_status.ssh_endpoint_status.errors
        )
        assert ssh_errors == [SshEndpointErrorKind.RSYNC_TOO_OLD]
        assert SyncErrorKind.ENDPOINT_HOST_ERRORS in status.errors

    def test_rsync_found_btrfs_not_found_on_destination(
        self, src_dst: tuple[Path, Path]
    ) -> None:
        _setup_active_sentinels(*src_dst)
        config, sync = _local_sync(*src_dst, dst_btrfs=True)

        status = _check(sync, config, fs_type="btrfs", missing_tools={"btrfs"})
        assert status.active is False
        ssh_errors = (
            status.destination_endpoint_status.volume_status.ssh_endpoint_status.errors
        )
        assert SshEndpointErrorKind.BTRFS_NOT_FOUND in ssh_errors

    def test_disabled_sync_does_not_add_tool_needs(
        self, src_dst: tuple[Path, Path]
    ) -> None:
        """A disabled btrfs sync must not make the host's missing btrfs fatal
        for the enabled syncs sharing it."""
        src, dst = src_dst
        _setup_active_sentinels(src, dst)
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

        result = check_all_syncs(
            config, run=healthy_host(), which=local_tools(missing={"btrfs"})
        )
        assert result.ssh_endpoint_statuses["localhost"].errors == []
        assert result.sync_statuses["plain"].active is True

    def test_stat_not_found_on_destination(self, src_dst: tuple[Path, Path]) -> None:
        _setup_active_sentinels(*src_dst)
        config, sync = _local_sync(*src_dst, dst_btrfs=True)

        status = _check(sync, config, fs_type="btrfs", missing_tools={"stat"})
        assert status.active is False
        ssh_errors = (
            status.destination_endpoint_status.volume_status.ssh_endpoint_status.errors
        )
        assert SshEndpointErrorKind.STAT_NOT_FOUND in ssh_errors
        dst_ep_errors = status.destination_endpoint_status.errors
        assert DestinationEndpointErrorKind.VOL_NOT_BTRFS not in dst_ep_errors
        assert (
            DestinationEndpointErrorKind.STAGING_NOT_BTRFS_SUBVOLUME
            not in dst_ep_errors
        )

    def test_findmnt_not_found_on_destination(self, src_dst: tuple[Path, Path]) -> None:
        src, dst = src_dst
        _setup_active_sentinels(src, dst)
        (dst / "backup" / "staging").mkdir()
        (dst / "backup" / "snapshots").mkdir()
        config, sync = _local_sync(src, dst, dst_btrfs=True)

        run = FakeCommandRunner()
        status = _check(
            sync, config, run=run, fs_type="btrfs", missing_tools={"findmnt"}
        )
        assert run.commands_starting_with("findmnt") == []
        assert status.active is False
        ssh_errors = (
            status.destination_endpoint_status.volume_status.ssh_endpoint_status.errors
        )
        assert SshEndpointErrorKind.FINDMNT_NOT_FOUND in ssh_errors
        dst_ep_errors = status.destination_endpoint_status.errors
        assert (
            DestinationEndpointErrorKind.VOL_NOT_MOUNTED_USER_SUBVOL_RM
            not in dst_ep_errors
        )

    def test_stat_and_findmnt_both_missing(self, src_dst: tuple[Path, Path]) -> None:
        _setup_active_sentinels(*src_dst)
        config, sync = _local_sync(*src_dst, dst_btrfs=True)

        status = _check(
            sync, config, fs_type="btrfs", missing_tools={"stat", "findmnt"}
        )
        assert status.active is False
        ssh_errors = (
            status.destination_endpoint_status.volume_status.ssh_endpoint_status.errors
        )
        assert SshEndpointErrorKind.STAT_NOT_FOUND in ssh_errors
        assert SshEndpointErrorKind.FINDMNT_NOT_FOUND in ssh_errors

    def test_destination_not_btrfs_filesystem(self, src_dst: tuple[Path, Path]) -> None:
        _setup_active_sentinels(*src_dst)
        config, sync = _local_sync(*src_dst, dst_btrfs=True)

        status = _check(sync, config, fs_type="ext2/ext3")
        assert status.active is False
        assert (
            DestinationEndpointErrorKind.VOL_NOT_BTRFS
            in status.destination_endpoint_status.errors
        )

    def test_destination_not_btrfs_subvolume(self, src_dst: tuple[Path, Path]) -> None:
        src, dst = src_dst
        _setup_active_sentinels(src, dst)
        # staging/ must exist for the subvolume check to run
        (dst / "backup" / "staging").mkdir(parents=True)
        config, sync = _local_sync(src, dst, dst_btrfs=True)
        run = FakeCommandRunner().on(
            "stat", "-c", "%i", returns=completed(stdout="1234\n")
        )

        status = _check(sync, config, run=run, fs_type="btrfs")
        assert status.active is False
        assert (
            DestinationEndpointErrorKind.STAGING_NOT_BTRFS_SUBVOLUME
            in status.destination_endpoint_status.errors
        )
        assert run.commands_starting_with("stat", "-c") == [
            ["stat", "-c", "%i", f"{dst}/backup/staging"]
        ]

    def test_destination_not_mounted_user_subvol_rm(
        self, src_dst: tuple[Path, Path]
    ) -> None:
        src, dst = src_dst
        _setup_active_sentinels(src, dst)
        (dst / "backup" / "staging").mkdir()
        (dst / "backup" / "snapshots").mkdir()
        config, sync = _local_sync(src, dst, dst_btrfs=True)
        run = FakeCommandRunner().on(
            "findmnt", "-T", str(dst), returns=completed(stdout="rw,relatime\n")
        )

        status = _check(sync, config, run=run, fs_type="btrfs")
        assert status.active is False
        assert (
            DestinationEndpointErrorKind.VOL_NOT_MOUNTED_USER_SUBVOL_RM
            in status.destination_endpoint_status.errors
        )

    def test_destination_latest_not_found(self, src_dst: tuple[Path, Path]) -> None:
        src, dst = src_dst
        _setup_active_sentinels(src, dst)
        # snapshots exists but staging does not
        (dst / "backup" / "snapshots").mkdir()
        config, sync = _local_sync(src, dst, dst_btrfs=True)

        status = _check(sync, config, fs_type="btrfs")
        assert status.active is False
        dst_ep_errors = status.destination_endpoint_status.errors
        assert DestinationEndpointErrorKind.STAGING_SUBVOL_NOT_FOUND in dst_ep_errors
        assert DestinationEndpointErrorKind.SNAPSHOTS_DIR_NOT_FOUND not in dst_ep_errors

    def test_destination_snapshots_dir_not_found(
        self, src_dst: tuple[Path, Path]
    ) -> None:
        src, dst = src_dst
        _setup_active_sentinels(src, dst)
        # staging/ exists but snapshots/ does not
        (dst / "backup" / "staging").mkdir()
        config, sync = _local_sync(src, dst, dst_btrfs=True)

        status = _check(sync, config, fs_type="btrfs")
        assert status.active is False
        dst_ep_errors = status.destination_endpoint_status.errors
        assert DestinationEndpointErrorKind.SNAPSHOTS_DIR_NOT_FOUND in dst_ep_errors
        assert (
            DestinationEndpointErrorKind.STAGING_SUBVOL_NOT_FOUND not in dst_ep_errors
        )

    def test_destination_latest_and_snapshots_both_missing(
        self, src_dst: tuple[Path, Path]
    ) -> None:
        _setup_active_sentinels(*src_dst)
        # neither staging nor snapshots exist
        config, sync = _local_sync(*src_dst, dst_btrfs=True)

        status = _check(sync, config, fs_type="btrfs")
        assert status.active is False
        dst_ep_errors = status.destination_endpoint_status.errors
        assert DestinationEndpointErrorKind.STAGING_SUBVOL_NOT_FOUND in dst_ep_errors
        assert DestinationEndpointErrorKind.SNAPSHOTS_DIR_NOT_FOUND in dst_ep_errors

    def test_btrfs_check_skipped_when_not_enabled(
        self, src_dst: tuple[Path, Path]
    ) -> None:
        _setup_active_sentinels(*src_dst)
        config, sync = _local_sync(*src_dst)
        run = FakeCommandRunner()

        status = _check(sync, config, run=run, fs_type="btrfs")
        assert status.active is True
        assert status.errors == []
        # No staging subvolume is probed.
        assert run.commands_starting_with("stat", "-c") == []

    def test_multiple_failures_accumulated(self, src_dst: tuple[Path, Path]) -> None:
        """Rsync missing on local volume: SSH endpoint reports the error,
        but the volume is still probed (local volumes don't cascade
        SSH_ENDPOINT_INACTIVE).  The sync is inactive because the source
        endpoint sentinel is missing."""
        src, dst = src_dst
        _setup_active_sentinels(src, dst)
        (src / "data" / ".nbkp-src").unlink()
        config, sync = _local_sync(src, dst)

        status = _check(sync, config, missing_tools={"rsync"})
        assert status.active is False
        ssh_errors = (
            status.source_endpoint_status.volume_status.ssh_endpoint_status.errors
        )
        assert SshEndpointErrorKind.RSYNC_NOT_FOUND in ssh_errors
        # Local volume is still probed despite SSH endpoint errors —
        # no SSH_ENDPOINT_INACTIVE cascade for local volumes
        assert status.source_endpoint_status.volume_status.active is True
        # Source endpoint is inactive because .nbkp-src sentinel is missing
        assert SourceEndpointErrorKind.SENTINEL_NOT_FOUND in (
            status.source_endpoint_status.errors
        )

    def test_both_volumes_unavailable(self, src_dst: tuple[Path, Path]) -> None:
        """Both source and destination unavailable."""
        # No .nbkp-vol sentinel files, so volumes are inactive
        config, sync = _local_sync(*src_dst)

        status = _check(sync, config)
        assert status.active is False
        src_vol_errors = status.source_endpoint_status.volume_status.errors
        dst_vol_errors = status.destination_endpoint_status.volume_status.errors
        assert VolumeErrorKind.SENTINEL_NOT_FOUND in src_vol_errors
        assert VolumeErrorKind.SENTINEL_NOT_FOUND in dst_vol_errors


def _sync_with_remote(
    local_root: Path, remote: str, *, dst_btrfs: bool = False
) -> tuple[Config, SyncConfig]:
    """``s1`` between a local volume at *local_root* and the remote volume
    *remote* (``"src"`` at ``src-server:/data``, or ``"dst"`` at
    ``dst-server:/backup``); endpoints in ``data`` (source) and ``backup``
    (destination), with active local sentinels."""
    server = f"{remote}-server"
    remote_vol = RemoteVolume(
        slug=remote,
        ssh_endpoint=server,
        path="/data" if remote == "src" else "/backup",
    )
    local = "dst" if remote == "src" else "src"
    (local_root / ".nbkp-vol").touch()
    local_subdir, sentinel = (
        ("backup", ".nbkp-dst") if local == "dst" else ("data", ".nbkp-src")
    )
    (local_root / local_subdir).mkdir()
    (local_root / local_subdir / sentinel).touch()
    sync = SyncConfig(slug="s1", source="ep-src", destination="ep-dst")
    config = Config(
        ssh_endpoints={server: SshEndpoint(slug=server, host=f"{remote}.local")},
        volumes={
            remote: remote_vol,
            local: LocalVolume(slug=local, path=str(local_root)),
        },
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src", subdir="data"),
            "ep-dst": SyncEndpoint(
                slug="ep-dst",
                volume="dst",
                subdir="backup",
                btrfs_snapshots=BtrfsSnapshotConfig(enabled=dst_btrfs),
            ),
        },
        syncs={"s1": sync},
    )
    return config, sync


class TestCheckSyncRemoteCommands:
    """A remote host answers every probe over SSH, ``which`` included."""

    def test_rsync_not_found_on_remote_source(self, tmp_path: Path) -> None:
        config, sync = _sync_with_remote(tmp_path, "src")
        run = FakeCommandRunner().on("which", "rsync", returns=FAILS, volume="src")

        status = _check(sync, config, resolved=_make_resolved(config), run=run)
        assert status.active is False
        ssh_errors = (
            status.source_endpoint_status.volume_status.ssh_endpoint_status.errors
        )
        assert SshEndpointErrorKind.RSYNC_NOT_FOUND in ssh_errors

    def test_rsync_not_found_on_remote_destination(self, tmp_path: Path) -> None:
        config, sync = _sync_with_remote(tmp_path, "dst")
        run = FakeCommandRunner().on("which", "rsync", returns=FAILS, volume="dst")

        status = _check(sync, config, resolved=_make_resolved(config), run=run)
        assert status.active is False
        ssh_errors = (
            status.destination_endpoint_status.volume_status.ssh_endpoint_status.errors
        )
        assert SshEndpointErrorKind.RSYNC_NOT_FOUND in ssh_errors

    def test_btrfs_not_found_on_remote_destination(self, tmp_path: Path) -> None:
        config, sync = _sync_with_remote(tmp_path, "dst", dst_btrfs=True)
        run = FakeCommandRunner().on("which", "btrfs", returns=FAILS, volume="dst")

        status = _check(
            sync, config, resolved=_make_resolved(config), run=run, fs_type="btrfs"
        )
        assert status.active is False
        ssh_errors = (
            status.destination_endpoint_status.volume_status.ssh_endpoint_status.errors
        )
        assert SshEndpointErrorKind.BTRFS_NOT_FOUND in ssh_errors

    def test_destination_not_btrfs_on_remote(self, tmp_path: Path) -> None:
        config, sync = _sync_with_remote(tmp_path, "dst", dst_btrfs=True)
        run = FakeCommandRunner().on(
            "stat", "-f", "-c", "%T", "/backup", returns=completed(stdout="ext4\n")
        )

        status = _check(
            sync, config, resolved=_make_resolved(config), run=run, fs_type="btrfs"
        )
        assert status.active is False
        assert (
            DestinationEndpointErrorKind.VOL_NOT_BTRFS
            in status.destination_endpoint_status.errors
        )

    def test_destination_not_subvolume_on_remote(self, tmp_path: Path) -> None:
        config, sync = _sync_with_remote(tmp_path, "dst", dst_btrfs=True)
        run = FakeCommandRunner().on(
            "stat",
            "-c",
            "%i",
            "/backup/backup/staging",
            returns=completed(stdout="1234\n"),
        )

        status = _check(
            sync, config, resolved=_make_resolved(config), run=run, fs_type="btrfs"
        )
        assert status.active is False
        assert (
            DestinationEndpointErrorKind.STAGING_NOT_BTRFS_SUBVOLUME
            in status.destination_endpoint_status.errors
        )

    def test_destination_not_mounted_user_subvol_rm_on_remote(
        self, tmp_path: Path
    ) -> None:
        config, sync = _sync_with_remote(tmp_path, "dst", dst_btrfs=True)
        run = FakeCommandRunner().on(
            "findmnt", "-T", "/backup", returns=completed(stdout="rw,relatime\n")
        )

        status = _check(
            sync, config, resolved=_make_resolved(config), run=run, fs_type="btrfs"
        )
        assert status.active is False
        assert (
            DestinationEndpointErrorKind.VOL_NOT_MOUNTED_USER_SUBVOL_RM
            in status.destination_endpoint_status.errors
        )

    def test_stat_not_found_on_remote_destination(self, tmp_path: Path) -> None:
        config, sync = _sync_with_remote(tmp_path, "dst", dst_btrfs=True)
        # Were stat probed, ext4 would report VOL_NOT_BTRFS.
        run = FakeCommandRunner().on("which", "stat", returns=FAILS, volume="dst")

        status = _check(
            sync, config, resolved=_make_resolved(config), run=run, fs_type="ext4"
        )
        assert status.active is False
        ssh_errors = (
            status.destination_endpoint_status.volume_status.ssh_endpoint_status.errors
        )
        assert SshEndpointErrorKind.STAT_NOT_FOUND in ssh_errors
        assert (
            DestinationEndpointErrorKind.VOL_NOT_BTRFS
            not in status.destination_endpoint_status.errors
        )

    def test_findmnt_not_found_on_remote_destination(self, tmp_path: Path) -> None:
        config, sync = _sync_with_remote(tmp_path, "dst", dst_btrfs=True)
        run = (
            FakeCommandRunner()
            .on("which", "findmnt", returns=FAILS, volume="dst")
            # Were findmnt probed, its options would lack user_subvol_rm_allowed.
            .on("findmnt", "-T", "/backup", returns=completed(stdout="rw\n"))
        )

        status = _check(
            sync, config, resolved=_make_resolved(config), run=run, fs_type="btrfs"
        )
        assert status.active is False
        ssh_errors = (
            status.destination_endpoint_status.volume_status.ssh_endpoint_status.errors
        )
        assert SshEndpointErrorKind.FINDMNT_NOT_FOUND in ssh_errors
        assert (
            DestinationEndpointErrorKind.VOL_NOT_MOUNTED_USER_SUBVOL_RM
            not in status.destination_endpoint_status.errors
        )

    def test_destination_latest_not_found_on_remote(self, tmp_path: Path) -> None:
        config, sync = _sync_with_remote(tmp_path, "dst", dst_btrfs=True)
        run = FakeCommandRunner().on(
            "test", "-d", "/backup/backup/staging", returns=FAILS
        )

        status = _check(
            sync, config, resolved=_make_resolved(config), run=run, fs_type="btrfs"
        )
        assert status.active is False
        assert (
            DestinationEndpointErrorKind.STAGING_SUBVOL_NOT_FOUND
            in status.destination_endpoint_status.errors
        )
        assert (
            DestinationEndpointErrorKind.SNAPSHOTS_DIR_NOT_FOUND
            not in status.destination_endpoint_status.errors
        )

    def test_destination_snapshots_dir_not_found_on_remote(
        self, tmp_path: Path
    ) -> None:
        config, sync = _sync_with_remote(tmp_path, "dst", dst_btrfs=True)
        run = FakeCommandRunner().on(
            "test", "-d", "/backup/backup/snapshots", returns=FAILS
        )

        status = _check(
            sync, config, resolved=_make_resolved(config), run=run, fs_type="btrfs"
        )
        assert status.active is False
        assert (
            DestinationEndpointErrorKind.SNAPSHOTS_DIR_NOT_FOUND
            in status.destination_endpoint_status.errors
        )
        assert (
            DestinationEndpointErrorKind.STAGING_SUBVOL_NOT_FOUND
            not in status.destination_endpoint_status.errors
        )


def _check_all(
    config: Config, run: FakeCommandRunner | None = None, **kwargs: Any
) -> PreflightResult:
    """``check_all_syncs`` with every host healthy but for *run*'s own
    answers, and no ``~/.ssh/config``."""
    return check_all_syncs(
        config,
        run=healthy_host(run),
        which=local_tools(),
        ssh_config=paramiko.SSHConfig(),
        **kwargs,
    )


def _standalone_probes(run: FakeCommandRunner) -> dict[str, ResolvedEndpoint | None]:
    """Endpoints probed for reachability only (``true``), with their route."""
    return {c.volume.slug: c.endpoint for c in run.calls if c.cmd == ["true"]}


class TestCheckAllSyncs:
    def test_check_all(self, tmp_path: Path) -> None:
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

        result = _check_all(config)
        assert result.volume_statuses["src"].active is True
        assert result.volume_statuses["dst"].active is True
        assert result.sync_statuses["s1"].active is True

    def test_only_syncs_filters_syncs_and_volumes(self, tmp_path: Path) -> None:
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

        run = FakeCommandRunner()
        result = _check_all(config, run, only_syncs=["s1"])
        assert set(result.sync_statuses.keys()) == {"s1"}
        assert set(result.volume_statuses.keys()) == {"src1", "dst1"}
        assert result.sync_statuses["s1"].active is True
        # The other volumes are not probed at all.
        assert {c.volume.slug for c in run.calls} <= {"src1", "dst1"}

    @pytest.mark.parametrize("only_syncs", [None, ["s1"]])
    def test_step_count_matches_reported_steps(
        self, only_syncs: list[str] | None, tmp_path: Path
    ) -> None:
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
        _check_all(config, on_check_start=started.append, only_syncs=only_syncs)
        assert check_step_count(config, only_syncs) == len(started)


class TestStandaloneEndpointProbing:
    """Tests for standalone SSH endpoint probing (bastions, orphans) in Phase 1."""

    def _make_bastion_config(self, tmp_path: Path) -> Config:
        """Config with a remote volume that goes through a bastion."""
        src = tmp_path / "src"
        src.mkdir(exist_ok=True)
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

    def _through_bastion(self, config: Config) -> ResolvedEndpoints:
        return {
            "remote": ResolvedEndpoint(
                server=config.ssh_endpoints["target"],
                proxy_chain=[config.ssh_endpoints["bastion"]],
            ),
        }

    def test_bastion_appears_in_ssh_statuses(self, tmp_path: Path) -> None:
        """Bastion endpoints appear in ssh_endpoint_statuses after check_all_syncs."""
        config = self._make_bastion_config(tmp_path)
        run = FakeCommandRunner()
        result = _check_all(
            config, run, resolved_endpoints=self._through_bastion(config)
        )
        assert "bastion" in result.ssh_endpoint_statuses
        assert result.ssh_endpoint_statuses["bastion"].active is True
        # No tool errors — bastions have default (empty) tool needs
        assert result.ssh_endpoint_statuses["bastion"].errors == []
        # host_tools is None — no tool probing on bastions
        assert result.ssh_endpoint_statuses["bastion"].diagnostics.host_tools is None
        assert [c.cmd for c in run.calls if c.volume.slug == "bastion"] == [["true"]]

    def test_unreachable_bastion(self, tmp_path: Path) -> None:
        """Unreachable bastion gets UNREACHABLE error."""
        config = self._make_bastion_config(tmp_path)
        run = FakeCommandRunner().on(
            "true", returns=TimeoutError("timed out"), volume="bastion"
        )
        result = _check_all(
            config, run, resolved_endpoints=self._through_bastion(config)
        )
        assert "bastion" in result.ssh_endpoint_statuses
        bastion_status = result.ssh_endpoint_statuses["bastion"]
        assert bastion_status.active is False
        assert SshEndpointErrorKind.UNREACHABLE in bastion_status.errors

    def test_multi_hop_bastion_chain(self, tmp_path: Path) -> None:
        """All hops in a multi-hop bastion chain appear in statuses."""
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

        run = FakeCommandRunner()
        result = _check_all(
            config,
            run,
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
        # Each hop is reached through the hops before it.
        assert _standalone_probes(run) == {
            "bastion1": ResolvedEndpoint(server=bastion1, proxy_chain=[]),
            "bastion2": ResolvedEndpoint(server=bastion2, proxy_chain=[bastion1]),
        }

    def test_no_bastions_when_no_proxy_jumps(self, tmp_path: Path) -> None:
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
        run = FakeCommandRunner()
        result = _check_all(config, run)
        # Only "localhost" — no standalone endpoints
        assert set(result.ssh_endpoint_statuses.keys()) == {"localhost"}
        assert _standalone_probes(run) == {}

    def test_orphan_endpoint_probed(self, tmp_path: Path) -> None:
        """Orphan endpoints (defined but not referenced by volumes) are probed."""
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
        run = FakeCommandRunner()
        result = _check_all(
            config,
            run,
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
        assert _standalone_probes(run) == {
            "orphan": ResolvedEndpoint(server=orphan, proxy_chain=[])
        }

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
            **self._through_bastion(config),
            "other": ResolvedEndpoint(server=config.ssh_endpoints["other-host"]),
        }

    def test_endpoint_at_excluded_location_not_probed(self, tmp_path: Path) -> None:
        config = self._make_alternate_config(tmp_path)
        resolved = self._resolved(config)
        run = FakeCommandRunner()
        result = _check_all(
            config, run, resolved_endpoints=resolved, exclude_locations=["home"]
        )
        assert set(_standalone_probes(run)) == {"bastion"}
        assert "other-lan" not in result.ssh_endpoint_statuses
        assert check_step_count(config, None, resolved, ["home"]) == (
            len(result.ssh_endpoint_statuses)
            + len(result.volume_statuses)
            + len(result.source_endpoint_statuses)
            + len(result.destination_endpoint_statuses)
        )

    def test_only_syncs_probes_only_endpoints_of_selected_volumes(
        self, tmp_path: Path
    ) -> None:
        config = self._make_alternate_config(tmp_path)
        run = FakeCommandRunner()
        result = _check_all(
            config,
            run,
            only_syncs=["s1"],
            resolved_endpoints=self._resolved(config),
        )
        assert set(_standalone_probes(run)) == {"bastion"}
        assert set(result.ssh_endpoint_statuses) == {
            "localhost",
            "target",
            "bastion",
        }


def _alternate_endpoint_config(laptop: Path, archive: str, photos: str) -> Config:
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
            "laptop": LocalVolume(slug="laptop", path=str(laptop)),
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
        self, archive: str, photos: str, tmp_path: Path
    ) -> None:
        config = _alternate_endpoint_config(tmp_path, archive, photos)
        resolved = resolve_all_endpoints(
            config,
            EndpointFilter(exclude_locations=["home"]),
            ssh_config=paramiko.SSHConfig(),
            lookup=lambda _host: {"203.0.113.7"},
        )
        run = FakeCommandRunner()

        result = _check_all(config, run, resolved_endpoints=resolved)

        # Nothing reaches the excluded volume; the other one goes over the WAN.
        assert archive not in {c.volume.slug for c in run.calls}
        routes = {
            c.endpoint.server.slug
            for c in run.calls
            if c.volume.slug == photos and c.endpoint is not None
        }
        assert routes == {"nas-wan"}
        assert result.volume_statuses[photos].active is True
        assert result.volume_statuses[photos].ssh_endpoint_status.slug == "nas-wan"
        assert result.ssh_endpoint_statuses["nas-wan"].active is True
        assert result.volume_statuses[archive].active is False
        assert result.ssh_endpoint_statuses["nas-lan"].errors == [
            SshEndpointErrorKind.LOCATION_EXCLUDED
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

    def test_snapshots_dir_not_found(
        self,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_active_sentinels(src, dst)
        # No snapshots dir

        config, sync = self._make_hl_config(src, dst)

        status = _check(sync, config)
        assert status.active is False
        assert (
            DestinationEndpointErrorKind.SNAPSHOTS_DIR_NOT_FOUND
            in status.destination_endpoint_status.errors
        )

    def test_no_hardlink_support_fat(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_active_sentinels(src, dst)
        (dst / "backup" / "snapshots").mkdir()

        config, sync = self._make_hl_config(src, dst)

        status = _check(sync, config, fs_type="vfat")
        assert status.active is False
        assert (
            DestinationEndpointErrorKind.VOL_NO_HARDLINK_SUPPORT
            in status.destination_endpoint_status.errors
        )

    def test_active_with_ext4(
        self,
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

        status = _check(sync, config)
        assert status.active is True
        assert status.errors == []

    def test_stat_not_found(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_active_sentinels(src, dst)

        config, sync = self._make_hl_config(src, dst)

        # Were stat probed, vfat would report VOL_NO_HARDLINK_SUPPORT.
        run = FakeCommandRunner()
        status = _check(sync, config, run=run, fs_type="vfat", missing_tools={"stat"})
        assert run.commands_starting_with("stat") == []
        assert status.active is False
        ssh_errors = (
            status.destination_endpoint_status.volume_status.ssh_endpoint_status.errors
        )
        assert SshEndpointErrorKind.STAT_NOT_FOUND in ssh_errors
        # No hardlink support check when stat is missing
        assert (
            DestinationEndpointErrorKind.VOL_NO_HARDLINK_SUPPORT
            not in status.destination_endpoint_status.errors
        )

    def test_no_btrfs_checks_for_hardlink(
        self,
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

        status = _check(sync, config)
        ssh_errors = (
            status.destination_endpoint_status.volume_status.ssh_endpoint_status.errors
        )
        assert SshEndpointErrorKind.BTRFS_NOT_FOUND not in ssh_errors
        assert (
            DestinationEndpointErrorKind.VOL_NOT_BTRFS
            not in status.destination_endpoint_status.errors
        )
        assert (
            DestinationEndpointErrorKind.STAGING_NOT_BTRFS_SUBVOLUME
            not in status.destination_endpoint_status.errors
        )
        assert (
            DestinationEndpointErrorKind.STAGING_SUBVOL_NOT_FOUND
            not in status.destination_endpoint_status.errors
        )

    def test_remote_no_hardlink_support(self, tmp_path: Path) -> None:
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
        run = FakeCommandRunner().on(
            "stat", "-f", returns=completed(stdout="vfat\n"), volume="dst"
        )

        status = _check(sync, config, run=run, resolved=_make_resolved(config))
        assert status.active is False
        assert (
            DestinationEndpointErrorKind.VOL_NO_HARDLINK_SUPPORT
            in status.destination_endpoint_status.errors
        )
        assert ["stat", "-f", "-c", "%T", "/backup"] in run.commands


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

    def test_btrfs_source_latest_missing(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_sentinels(src, dst)
        # No latest/ under source

        config, sync = self._make_config(src, dst, "btrfs")

        status = _check(sync, config)
        assert (
            SourceEndpointErrorKind.LATEST_SYMLINK_NOT_FOUND
            in status.source_endpoint_status.errors
        )

    def test_hard_link_source_latest_missing(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_sentinels(src, dst)
        # No latest/ under source

        config, sync = self._make_config(src, dst, "hard-link")

        status = _check(sync, config)
        assert (
            SourceEndpointErrorKind.LATEST_SYMLINK_NOT_FOUND
            in status.source_endpoint_status.errors
        )

    def test_btrfs_source_latest_present(self, tmp_path: Path) -> None:
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

        status = _check(sync, config)
        assert (
            SourceEndpointErrorKind.LATEST_SYMLINK_NOT_FOUND
            not in status.source_endpoint_status.errors
        )
        assert (
            SourceEndpointErrorKind.LATEST_SYMLINK_INVALID
            not in status.source_endpoint_status.errors
        )

    def test_hard_link_source_latest_symlink(self, tmp_path: Path) -> None:
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

        status = _check(sync, config)
        assert (
            SourceEndpointErrorKind.LATEST_SYMLINK_NOT_FOUND
            not in status.source_endpoint_status.errors
        )

    def test_no_snapshots_source_skips_check(self, tmp_path: Path) -> None:
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

        status = _check(sync, config)
        assert (
            SourceEndpointErrorKind.LATEST_SYMLINK_NOT_FOUND
            not in status.source_endpoint_status.errors
        )
        # A source without snapshots has no layout to observe.
        assert status.source_endpoint_status.diagnostics is not None
        assert status.source_endpoint_status.diagnostics.latest is None


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

    def test_btrfs_source_snapshots_missing(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_sentinels(src, dst)
        (src / "data" / "latest").symlink_to("/dev/null")
        # No snapshots/ dir

        config, sync = self._make_config(src, dst, "btrfs")

        status = _check(sync, config)
        assert (
            SourceEndpointErrorKind.SNAPSHOTS_DIR_NOT_FOUND
            in status.source_endpoint_status.errors
        )

    def test_hard_link_source_snapshots_missing(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_sentinels(src, dst)
        (src / "data" / "latest").symlink_to("/dev/null")
        # No snapshots/ dir

        config, sync = self._make_config(src, dst, "hard-link")

        status = _check(sync, config)
        assert (
            SourceEndpointErrorKind.SNAPSHOTS_DIR_NOT_FOUND
            in status.source_endpoint_status.errors
        )

    def test_btrfs_source_snapshots_present(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_sentinels(src, dst)
        (src / "data" / "snapshots").mkdir()
        (src / "data" / "latest").symlink_to("/dev/null")

        config, sync = self._make_config(src, dst, "btrfs")

        status = _check(sync, config)
        assert (
            SourceEndpointErrorKind.SNAPSHOTS_DIR_NOT_FOUND
            not in status.source_endpoint_status.errors
        )

    def test_no_snapshots_source_skips_check(self, tmp_path: Path) -> None:
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

        status = _check(sync, config)
        assert (
            SourceEndpointErrorKind.SNAPSHOTS_DIR_NOT_FOUND
            not in status.source_endpoint_status.errors
        )
        assert status.source_endpoint_status.diagnostics is not None
        assert status.source_endpoint_status.diagnostics.snapshot_dirs is None


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

    def _make_upstream_config(self, tmp_path: Path) -> Config:
        """s1 reads endpoint ep-up-src, whose latest → /dev/null, and
        upstream writes it."""
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

        return Config(
            volumes={
                "src": LocalVolume(slug="src", path=str(src)),
                "dst": LocalVolume(slug="dst", path=str(dst)),
                "upstream": LocalVolume(slug="upstream", path=str(upstream_src)),
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
            syncs={
                "s1": SyncConfig(
                    slug="s1", source="ep-up-src", destination="ep-up-dst"
                ),
                "upstream": SyncConfig(
                    slug="upstream", source="ep-up-usrc", destination="ep-up-src"
                ),
            },
        )

    def test_source_devnull_accepted_with_upstream(
        self,
        tmp_path: Path,
    ) -> None:
        """Source latest → /dev/null is valid when an upstream
        sync writes to this source endpoint."""
        config = self._make_upstream_config(tmp_path)
        status = _check_all(config).sync_statuses["s1"]
        assert (
            SourceEndpointErrorKind.LATEST_SYMLINK_INVALID
            not in status.source_endpoint_status.errors
        )
        assert (
            SourceEndpointErrorKind.LATEST_SYMLINK_NOT_FOUND
            not in status.source_endpoint_status.errors
        )
        assert status.errors == []

    def test_source_devnull_rejected_when_upstream_not_selected(
        self,
        tmp_path: Path,
    ) -> None:
        """An upstream sync left out by --sync never replaces the /dev/null
        marker, so the downstream sync would read /dev/null."""
        config = self._make_upstream_config(tmp_path)
        status = _check_all(config, only_syncs=["s1"]).sync_statuses["s1"]
        assert SyncErrorKind.SRC_EP_LATEST_DEVNULL_NO_UPSTREAM in status.errors

    def test_source_devnull_inactive_in_dry_run_with_upstream(
        self,
        tmp_path: Path,
    ) -> None:
        """Source latest → /dev/null with upstream sync is marked
        inactive in dry-run mode because the upstream won't create
        a real snapshot."""
        config = self._make_upstream_config(tmp_path)
        status = _check_all(config, dry_run=True).sync_statuses["s1"]
        assert SyncErrorKind.DRY_RUN_SRC_EP_SNAPSHOT_PENDING in status.errors
        assert not status.active

    def test_source_devnull_rejected_without_upstream(
        self,
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

        status = _check(sync, config)
        assert SyncErrorKind.SRC_EP_LATEST_DEVNULL_NO_UPSTREAM in status.errors

    def test_source_dangling_symlink_invalid(
        self,
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

        status = _check(sync, config)
        assert (
            SourceEndpointErrorKind.LATEST_SYMLINK_INVALID
            in status.source_endpoint_status.errors
        )

    def test_dest_devnull_accepted(
        self,
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

        status = _check(sync, config)
        assert (
            DestinationEndpointErrorKind.LATEST_SYMLINK_NOT_FOUND
            not in status.destination_endpoint_status.errors
        )
        assert (
            DestinationEndpointErrorKind.LATEST_SYMLINK_INVALID
            not in status.destination_endpoint_status.errors
        )

    def test_dest_latest_missing(
        self,
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

        status = _check(sync, config)
        assert (
            DestinationEndpointErrorKind.LATEST_SYMLINK_NOT_FOUND
            in status.destination_endpoint_status.errors
        )

    def test_dest_dangling_symlink_invalid(
        self,
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

        status = _check(sync, config)
        assert (
            DestinationEndpointErrorKind.LATEST_SYMLINK_INVALID
            in status.destination_endpoint_status.errors
        )


class TestCheckRemoteVolumeSpaces:
    def test_active(self) -> None:
        vol, config = _remote_config(path="/my backup")
        resolved = _make_resolved(config)
        run = healthy_host()
        status = check_volume(vol, resolved, run=run)
        assert status.active is True
        assert status.diagnostics.capabilities is not None
        assert run.commands_starting_with("test", "-f") == [
            ["test", "-f", "/my backup/.nbkp-vol"]
        ]


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


def _nas_and_local_config(tmp_path: Path) -> Config:
    """``remote``: NAS → local ``a``; ``local``: local ``b`` → local ``c``.

    The local volumes carry their sentinels, so only the NAS can fail.
    """
    for name, sentinel in (("a", ".nbkp-dst"), ("b", ".nbkp-src"), ("c", ".nbkp-dst")):
        (tmp_path / name).mkdir()
        (tmp_path / name / ".nbkp-vol").touch()
        (tmp_path / name / sentinel).touch()
    return Config(
        ssh_endpoints={"nas": SshEndpoint(slug="nas", host="nas.example")},
        volumes={
            "nas": RemoteVolume(slug="nas", ssh_endpoint="nas", path="/data"),
            "a": LocalVolume(slug="a", path=str(tmp_path / "a")),
            "b": LocalVolume(slug="b", path=str(tmp_path / "b")),
            "c": LocalVolume(slug="c", path=str(tmp_path / "c")),
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


class TestConnectionLostDuringPreflight:
    """A host that drops after the first probe is unreachable for that host only.

    Every remote command opens its own connection, so the NAS can answer the
    host-tool probes and then go away (asleep, Wi-Fi dropped).
    """

    def test_during_volume_observation(self, tmp_path: Path) -> None:
        config = _nas_and_local_config(tmp_path)
        run = FakeCommandRunner().on(
            "test", "-f", returns=EOFError("connection closed by peer"), volume="nas"
        )

        result = _check_all(config, run, resolved_endpoints=_make_resolved(config))

        nas = result.volume_statuses["nas"]
        assert nas.errors == [VolumeErrorKind.SSH_ENDPOINT_INACTIVE]
        assert nas.ssh_endpoint_status.errors == [SshEndpointErrorKind.UNREACHABLE]
        assert nas.ssh_endpoint_status.diagnostics.ssh_error == (
            "connection closed by peer"
        )
        assert result.sync_statuses["remote"].is_expected_inactive()
        assert result.sync_statuses["local"].active

    def test_during_endpoint_observation(self, tmp_path: Path) -> None:
        config = _nas_and_local_config(tmp_path)
        run = FakeCommandRunner().on(
            "test",
            "-f",
            "/data/.nbkp-src",
            returns=paramiko.SSHException("Error reading SSH protocol banner"),
        )

        result = _check_all(config, run, resolved_endpoints=_make_resolved(config))

        assert result.volume_statuses["nas"].active
        src = result.source_endpoint_statuses["ep-nas"]
        assert src.errors == [SourceEndpointErrorKind.VOLUME_INACTIVE]
        assert src.volume_status.ssh_endpoint_status.errors == [
            SshEndpointErrorKind.UNREACHABLE
        ]
        assert result.sync_statuses["remote"].is_expected_inactive()
        assert result.sync_statuses["local"].active

    def test_during_destination_endpoint_observation(self, tmp_path: Path) -> None:
        base = _nas_and_local_config(tmp_path)
        config = Config(
            ssh_endpoints=base.ssh_endpoints,
            volumes=base.volumes,
            sync_endpoints={
                **{
                    slug: ep
                    for slug, ep in base.sync_endpoints.items()
                    if slug != "ep-nas"
                },
                "ep-nas-dst": SyncEndpoint(
                    slug="ep-nas-dst", volume="nas", subdir="backup"
                ),
            },
            syncs={
                "push": SyncConfig(
                    slug="push", source="ep-b", destination="ep-nas-dst"
                ),
                "local": base.syncs["local"],
            },
        )
        run = FakeCommandRunner().on(
            "test",
            "-f",
            "/data/backup/.nbkp-dst",
            returns=paramiko.SSHException("Error reading SSH protocol banner"),
        )

        result = _check_all(config, run, resolved_endpoints=_make_resolved(config))

        assert result.volume_statuses["nas"].active
        dst = result.destination_endpoint_statuses["ep-nas-dst"]
        assert dst.errors == [DestinationEndpointErrorKind.VOLUME_INACTIVE]
        assert dst.volume_status.ssh_endpoint_status.errors == [
            SshEndpointErrorKind.UNREACHABLE
        ]
        assert result.sync_statuses["push"].is_expected_inactive()
        assert result.sync_statuses["local"].active

    def test_local_errors_still_propagate(self, tmp_path: Path) -> None:
        config = _nas_and_local_config(tmp_path)
        run = FakeCommandRunner().on(
            "stat", "-f", returns=OSError("disk gone"), volume="b"
        )
        with pytest.raises(OSError, match="disk gone"):
            _check_all(config, run, resolved_endpoints=_make_resolved(config))

    def test_during_mount_tool_probe(self, tmp_path: Path) -> None:
        config = _nas_and_local_config(tmp_path)
        # The host tools answer; the connection drops at the mount tools.
        run = healthy_host(
            FakeCommandRunner().on(
                "which", "udisksctl", returns=EOFError("connection closed by peer")
            )
        )

        diag = observe_ssh_endpoint(
            config.volumes["nas"],
            _make_resolved(config),
            probe_mount_tools=True,
            run=run,
        )

        assert diag.ssh_reachable is False
        assert diag.ssh_error == "connection closed by peer"
        assert ["which", "rsync"] in run.commands
