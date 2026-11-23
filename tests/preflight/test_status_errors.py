"""
Tests for SSH-endpoint and sync-level error interpretation.

Covers auth failures, warnings, host-tool errors on localhost
(``SyncErrorKind.ENDPOINT_HOST_ERRORS``), the observation layer's exception
handling, and the ``latest`` symlink target parsing.
"""

from pathlib import Path

import paramiko
import pydantic
import pytest

from nbkp.config import (
    BtrfsSnapshotConfig,
    HardLinkSnapshotConfig,
    LocalVolume,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.disks import MountToolCapabilities
from nbkp.policy import Strictness
from nbkp.preflight.endpoint_checks import (
    observe_destination_endpoint,
    observe_source_endpoint,
)
from nbkp.preflight.ssh_checks import observe_standalone_endpoint
from nbkp.preflight.status.endpoint import (
    DestinationEndpointDiagnostics,
    DestinationEndpointErrorKind,
    DestinationEndpointStatus,
    DestinationEndpointWarningKind,
    LatestSymlinkState,
    SourceEndpointDiagnostics,
    SourceEndpointStatus,
)
from nbkp.preflight.status.ssh import (
    INACTIVE_SSH_ERRORS,
    HostToolCapabilities,
    SshEndpointDiagnostics,
    SshEndpointErrorKind,
    SshEndpointStatus,
    SshEndpointToolNeeds,
    SshEndpointWarningKind,
)
from nbkp.preflight.status.sync import SyncErrorKind, SyncStatus
from nbkp.preflight.status.volume import (
    VolumeCapabilities,
    VolumeDiagnostics,
    VolumeStatus,
)
from nbkp.preflight.strictness import has_fatal_errors
from nbkp.preflight.volume_checks import observe_ssh_endpoint
from nbkp.remote.endpoints import ResolvedEndpoint
from tests.fakes import BodyError, FakeCommandRunner, completed

_TOOLS = HostToolCapabilities(
    has_rsync=True,
    rsync_version_ok=True,
    has_btrfs=True,
    has_stat=True,
    has_findmnt=True,
)


def _ssh(
    diag: SshEndpointDiagnostics,
    needs: SshEndpointToolNeeds | None = None,
    slug: str = "localhost",
) -> SshEndpointStatus:
    return SshEndpointStatus.from_diagnostics(
        slug=slug,
        diagnostics=diag,
        needs=needs or SshEndpointToolNeeds(),
    )


class TestSshEndpointErrors:
    def test_auth_failure_is_its_own_non_inactive_error(self) -> None:
        status = _ssh(
            SshEndpointDiagnostics(
                ssh_reachable=False,
                ssh_auth_failed=True,
                ssh_error="Authentication failed.",
            ),
            slug="nas",
        )
        assert status.errors == [SshEndpointErrorKind.AUTH_FAILED]
        assert SshEndpointErrorKind.AUTH_FAILED not in INACTIVE_SSH_ERRORS

    def test_unreachable_keeps_cause(self) -> None:
        status = _ssh(
            SshEndpointDiagnostics(ssh_reachable=False, ssh_error="timed out"),
            slug="nas",
        )
        assert status.errors == [SshEndpointErrorKind.UNREACHABLE]
        assert status.diagnostics.ssh_error == "timed out"

    def test_findmnt_reported_once(self) -> None:
        """Needed by btrfs endpoints *and* mount management: one error."""
        status = _ssh(
            SshEndpointDiagnostics(
                host_tools=_TOOLS.model_copy(update={"has_findmnt": False}),
                mount_tools=MountToolCapabilities(
                    has_udisksctl=True,
                    udisksd_running=True,
                    has_findmnt=False,
                    has_lsblk=True,
                ),
            ),
            SshEndpointToolNeeds(
                has_btrfs_endpoints=True,
                has_snapshot_endpoints=True,
                has_mount_volumes=True,
            ),
        )
        assert status.errors == [SshEndpointErrorKind.FINDMNT_NOT_FOUND]

    def test_btrfs_module_is_a_warning_not_an_error(self) -> None:
        status = _ssh(
            SshEndpointDiagnostics(
                host_tools=_TOOLS,
                mount_tools=MountToolCapabilities(
                    has_udisksctl=True,
                    udisksd_running=True,
                    has_btrfs_module=False,
                    has_findmnt=True,
                    has_lsblk=True,
                ),
            ),
            SshEndpointToolNeeds(has_mount_volumes=True, has_btrfs_mount=True),
        )
        assert status.errors == []
        assert status.active is True
        assert status.warnings == [SshEndpointWarningKind.UDISKS_BTRFS_MODULE_MISSING]

    def test_statuses_are_frozen(self) -> None:
        status = _ssh(SshEndpointDiagnostics(host_tools=_TOOLS))
        with pytest.raises(pydantic.ValidationError):
            status.errors = [SshEndpointErrorKind.UNREACHABLE]


def _sync_status(ssh_status: SshEndpointStatus) -> SyncStatus:
    """Build a local→local sync whose volumes/endpoints are healthy."""
    src_vol = LocalVolume(slug="src", path="/mnt/src")
    dst_vol = LocalVolume(slug="dst", path="/mnt/dst")
    caps = VolumeCapabilities(
        sentinel_exists=True,
        is_btrfs_filesystem=False,
        hardlink_supported=True,
        btrfs_user_subvol_rm=False,
    )
    src_vs, dst_vs = (
        VolumeStatus.from_diagnostics(
            slug=vol.slug,
            config=vol,
            ssh_endpoint_status=ssh_status,
            diagnostics=VolumeDiagnostics(capabilities=caps),
        )
        for vol in (src_vol, dst_vol)
    )
    src_ep = SyncEndpoint(slug="ep-src", volume="src")
    dst_ep = SyncEndpoint(slug="ep-dst", volume="dst")
    sync = SyncConfig(slug="s", source="ep-src", destination="ep-dst")
    return SyncStatus.from_diagnostics(
        sync=sync,
        src_endpoint=src_ep,
        src_ep_status=SourceEndpointStatus.from_diagnostics(
            src_ep,
            src_vs,
            SourceEndpointDiagnostics(endpoint_slug="ep-src", sentinel_exists=True),
        ),
        dst_ep_status=DestinationEndpointStatus.from_diagnostics(
            dst_ep,
            dst_vs,
            DestinationEndpointDiagnostics(
                endpoint_slug="ep-dst",
                sentinel_exists=True,
                endpoint_writable=True,
            ),
        ),
        selected_syncs={"s": sync},
        dry_run=False,
    )


class TestEndpointHostErrors:
    def test_localhost_tool_error_makes_sync_inactive_and_fatal(self) -> None:
        """Regression: localhost RSYNC_NOT_FOUND used to leave syncs active."""
        ssh = _ssh(
            SshEndpointDiagnostics(
                host_tools=_TOOLS.model_copy(
                    update={"has_rsync": False, "rsync_version_ok": False},
                ),
            ),
        )
        status = _sync_status(ssh)
        assert status.errors == [SyncErrorKind.ENDPOINT_HOST_ERRORS]
        assert status.active is False
        assert status.is_expected_inactive() is False
        assert has_fatal_errors({"s": status}) is True
        assert (
            has_fatal_errors({"s": status}, strictness=Strictness.IGNORE_ALL) is False
        )

    def test_warning_alone_keeps_sync_active(self) -> None:
        ssh = _ssh(
            SshEndpointDiagnostics(
                host_tools=_TOOLS,
                mount_tools=MountToolCapabilities(
                    has_udisksctl=True,
                    udisksd_running=True,
                    has_btrfs_module=False,
                    has_findmnt=True,
                    has_lsblk=True,
                ),
            ),
            SshEndpointToolNeeds(has_mount_volumes=True, has_btrfs_mount=True),
        )
        assert _sync_status(ssh).active is True

    def test_healthy_host_adds_nothing(self) -> None:
        assert (
            _sync_status(_ssh(SshEndpointDiagnostics(host_tools=_TOOLS))).errors == []
        )


def _remote_volume() -> tuple[RemoteVolume, dict[str, ResolvedEndpoint]]:
    server = SshEndpoint(slug="nas", host="nas.local")
    vol = RemoteVolume(slug="data", ssh_endpoint="nas", path="/data")
    return vol, {"data": ResolvedEndpoint(server=server, proxy_chain=[])}


def _host_raising(error: Exception) -> FakeCommandRunner:
    """Script a host whose first probe (``which rsync``) fails with *error*."""
    return FakeCommandRunner().on("which", returns=error)


class TestObservationExceptions:
    def test_auth_failure_recorded(self) -> None:
        vol, resolved = _remote_volume()
        run = _host_raising(paramiko.AuthenticationException("Authentication failed."))
        diag = observe_ssh_endpoint(vol, resolved, run=run)
        assert diag.ssh_reachable is False
        assert diag.ssh_auth_failed is True
        assert diag.ssh_error == "Authentication failed."

    def test_host_key_mismatch_is_auth_failure(self) -> None:
        vol, resolved = _remote_volume()
        run = _host_raising(
            paramiko.BadHostKeyException(
                "nas.local",
                paramiko.RSAKey.generate(1024),
                paramiko.RSAKey.generate(1024),
            ),
        )
        assert observe_ssh_endpoint(vol, resolved, run=run).ssh_auth_failed is True

    def test_timeout_is_unreachable(self) -> None:
        vol, resolved = _remote_volume()
        diag = observe_ssh_endpoint(
            vol,
            resolved,
            run=_host_raising(TimeoutError("timed out")),
        )
        assert diag.ssh_reachable is False
        assert diag.ssh_auth_failed is False
        assert diag.ssh_error == "timed out"

    def test_programming_error_propagates(self) -> None:
        vol, resolved = _remote_volume()
        with pytest.raises(KeyError):
            observe_ssh_endpoint(vol, resolved, run=_host_raising(KeyError("bug")))

    def test_standalone_probe_reaches_the_server_through_its_proxy_chain(
        self,
    ) -> None:
        server = SshEndpoint(slug="b", host="b")
        bastion = SshEndpoint(slug="j", host="j")
        run = FakeCommandRunner()
        diag = observe_standalone_endpoint(server, [bastion], run=run)
        assert diag.ssh_reachable is True
        assert run.commands == [["true"]]
        assert run.calls[0].endpoint == ResolvedEndpoint(
            server=server,
            proxy_chain=[bastion],
        )

    def test_standalone_auth_failure(self) -> None:
        run = FakeCommandRunner().on(
            "true",
            returns=paramiko.AuthenticationException("Authentication failed."),
        )
        diag = observe_standalone_endpoint(SshEndpoint(slug="b", host="b"), [], run=run)
        assert diag.ssh_auth_failed is True

    def test_standalone_programming_error_propagates(self) -> None:
        run = FakeCommandRunner().on("true", returns=BodyError())
        with pytest.raises(BodyError):
            observe_standalone_endpoint(SshEndpoint(slug="b", host="b"), [], run=run)


class TestLatestSymlinkTarget:
    def _state(self, tmp_path: Path, target: str) -> LatestSymlinkState:
        """Return the ``latest`` a source endpoint at *tmp_path* reports."""
        vol = LocalVolume(slug="v", path=str(tmp_path))
        endpoint = SyncEndpoint(
            slug="ep",
            volume="v",
            hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
        )
        (tmp_path / "latest").symlink_to(target)
        diag = observe_source_endpoint(endpoint, vol, {}, run=FakeCommandRunner())
        assert diag.latest is not None
        return diag.latest

    def test_non_snapshot_directory_is_invalid(self, tmp_path: Path) -> None:
        """Regression: ``latest -> snapshots/foo`` used to raise ValueError."""
        (tmp_path / "snapshots" / "foo").mkdir(parents=True)
        state = self._state(tmp_path, "snapshots/foo")
        assert state.exists is True
        assert state.target_valid is False
        assert state.snapshot is None

    def test_snapshot_directory_is_valid(self, tmp_path: Path) -> None:
        (tmp_path / "snapshots" / "2026-03-06T14:30:00.000Z").mkdir(parents=True)
        state = self._state(tmp_path, "snapshots/2026-03-06T14:30:00.000Z")
        assert state.target_valid is True
        assert state.snapshot is not None
        assert state.snapshot.name == "2026-03-06T14:30:00.000Z"

    @pytest.mark.parametrize(
        "target",
        [
            "../other/snapshots/2026-03-06T14:30:00.000Z",
            "elsewhere/2026-03-06T14:30:00.000Z",
        ],
    )
    def test_snapshot_outside_snapshots_dir_is_invalid(
        self,
        tmp_path: Path,
        target: str,
    ) -> None:
        """Only ``snapshots/<name>`` is a snapshot of this endpoint."""
        (tmp_path / "ep").mkdir()
        (tmp_path / "ep" / "snapshots" / "2026-03-06T14:30:00.000Z").mkdir(parents=True)
        (tmp_path / "other" / "snapshots" / "2026-03-06T14:30:00.000Z").mkdir(
            parents=True,
        )
        (tmp_path / "ep" / "elsewhere" / "2026-03-06T14:30:00.000Z").mkdir(parents=True)
        state = self._state(tmp_path / "ep", target)
        assert state.target_valid is False
        assert state.snapshot is None

    def test_validity_unknown_when_target_is_dev_null(self, tmp_path: Path) -> None:
        state = self._state(tmp_path, "/dev/null")
        assert state.raw_target == "/dev/null"
        assert state.target_valid is None


_SNAPSHOT = "2024-01-01T00:00:00.000Z"
_BTRFS_CAPS = VolumeCapabilities(
    sentinel_exists=True,
    is_btrfs_filesystem=True,
    hardlink_supported=True,
    btrfs_user_subvol_rm=True,
)


class TestLatestSnapshotReadOnly:
    """
    A writable ``latest`` snapshot on btrfs destinations is a warning.

    It is probed on btrfs destinations, and the warning leaves the endpoint active.
    """

    def _observe(
        self,
        run: FakeCommandRunner,
        *,
        btrfs: bool = True,
        latest: str = f"snapshots/{_SNAPSHOT}",
    ) -> DestinationEndpointDiagnostics:
        vol, resolved = _remote_volume()
        endpoint = SyncEndpoint(
            slug="ep",
            volume="data",
            btrfs_snapshots=BtrfsSnapshotConfig(enabled=btrfs),
            hard_link_snapshots=HardLinkSnapshotConfig(enabled=not btrfs),
        )
        return observe_destination_endpoint(
            endpoint,
            vol,
            _BTRFS_CAPS,
            resolved,
            host_tools=_TOOLS,
            run=run.on("readlink", returns=completed(0, f"{latest}\n")),
        )

    @pytest.mark.parametrize(
        ("stdout", "readonly"),
        [("ro=true\n", True), ("ro=false\n", False)],
    )
    def test_probes_the_snapshot_latest_points_to(
        self,
        stdout: str,
        *,
        readonly: bool,
    ) -> None:
        run = FakeCommandRunner().on("btrfs", "property", returns=completed(0, stdout))
        assert self._observe(run).latest_snapshot_readonly is readonly
        assert run.commands_starting_with("btrfs") == [
            ["btrfs", "property", "get", f"/data/snapshots/{_SNAPSHOT}", "ro"],
        ]

    def test_a_failed_probe_is_unknown(self) -> None:
        run = FakeCommandRunner().on("btrfs", "property", returns=completed(1))
        assert self._observe(run).latest_snapshot_readonly is None

    def test_not_probed_for_hard_link_snapshots(self) -> None:
        run = FakeCommandRunner()
        assert self._observe(run, btrfs=False).latest_snapshot_readonly is None
        assert run.commands_starting_with("btrfs") == []

    def test_not_probed_while_latest_is_dev_null(self) -> None:
        run = FakeCommandRunner()
        diag = self._observe(run, latest="/dev/null")
        assert diag.latest_snapshot_readonly is None
        assert run.commands_starting_with("btrfs") == []

    @pytest.mark.parametrize(
        ("readonly", "warnings"),
        [
            (False, [DestinationEndpointWarningKind.LATEST_SNAPSHOT_WRITABLE]),
            (True, []),
            (None, []),
        ],
    )
    def test_writable_is_a_warning_not_an_error(
        self,
        *,
        readonly: bool | None,
        warnings: list[DestinationEndpointWarningKind],
    ) -> None:
        vol_status = VolumeStatus(
            slug="dst",
            config=LocalVolume(slug="dst", path="/mnt/dst"),
            ssh_endpoint_status=_ssh(SshEndpointDiagnostics(host_tools=_TOOLS)),
            diagnostics=VolumeDiagnostics(capabilities=_BTRFS_CAPS),
            errors=[],
        )
        status = DestinationEndpointStatus.from_diagnostics(
            SyncEndpoint(slug="ep", volume="dst"),
            vol_status,
            DestinationEndpointDiagnostics(
                endpoint_slug="ep",
                sentinel_exists=True,
                endpoint_writable=True,
                latest_snapshot_readonly=readonly,
            ),
        )
        assert (status.warnings, status.errors, status.active) == (
            warnings,
            [],
            True,
        )


class TestFsTypeUnknown:
    def test_unknown_fs_type_is_not_vol_not_btrfs(self) -> None:
        vol_status = VolumeStatus(
            slug="dst",
            config=LocalVolume(slug="dst", path="/mnt/dst"),
            ssh_endpoint_status=_ssh(SshEndpointDiagnostics(host_tools=_TOOLS)),
            diagnostics=VolumeDiagnostics(
                capabilities=VolumeCapabilities(
                    sentinel_exists=True,
                    is_btrfs_filesystem=None,
                    hardlink_supported=True,
                    btrfs_user_subvol_rm=False,
                ),
            ),
            errors=[],
        )
        status = DestinationEndpointStatus.from_diagnostics(
            SyncEndpoint(
                slug="ep",
                volume="dst",
                btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
            ),
            vol_status,
            DestinationEndpointDiagnostics(
                endpoint_slug="ep",
                sentinel_exists=True,
                endpoint_writable=True,
            ),
        )
        assert DestinationEndpointErrorKind.VOL_FS_TYPE_UNKNOWN in status.errors
        assert DestinationEndpointErrorKind.VOL_NOT_BTRFS not in status.errors


class TestDisabledSyncNeverFatal:
    """Regression: a disabled sync aborted `nbkp run` under the default mode."""

    def _disabled(self) -> dict[str, SyncStatus]:
        status = _sync_status(_ssh(SshEndpointDiagnostics(host_tools=_TOOLS)))
        disabled = status.model_copy(
            update={
                "config": status.config.model_copy(update={"enabled": False}),
                "errors": [SyncErrorKind.DISABLED],
            },
        )
        return {"s": disabled}

    @pytest.mark.parametrize("strictness", list(Strictness))
    def test_not_fatal_under_any_strictness(self, strictness: Strictness) -> None:
        assert has_fatal_errors(self._disabled(), strictness=strictness) is False

    def test_expected_inactive(self) -> None:
        assert self._disabled()["s"].is_expected_inactive() is True
