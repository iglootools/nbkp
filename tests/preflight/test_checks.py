"""Tests for nbkp.preflight.checks."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    RemoteVolume,
    ResolvedEndpoint,
    ResolvedEndpoints,
    RsyncOptions,
    SshEndpoint,
    SshConnectionOptions,
    SyncConfig,
    SyncEndpoint,
    resolve_proxy_chain,
)
from nbkp.output import OutputFormat
from nbkp.sync import SyncResult
from nbkp.preflight.checks import (
    SyncReason,
    SyncStatus,
    VolumeReason,
    VolumeStatus,
    _check_btrfs_filesystem,
    _check_btrfs_mount_option,
    _check_btrfs_subvolume,
    _check_command_available,
    _check_rsync_version,
    check_all_syncs,
    check_sync,
    check_volume,
    parse_rsync_version,
)


class TestLocalVolume:
    def test_construction(self) -> None:
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert vol.slug == "data"
        assert vol.path == "/mnt/data"

    def test_frozen(self) -> None:
        import pydantic

        vol = LocalVolume(slug="data", path="/mnt/data")
        try:
            vol.slug = "other"  # type: ignore[misc]
            assert False, "Should be frozen"
        except (AttributeError, pydantic.ValidationError):
            pass


class TestSshEndpoint:
    def test_construction_defaults(self) -> None:
        server = SshEndpoint(slug="nas-server", host="nas.local")
        assert server.slug == "nas-server"
        assert server.host == "nas.local"
        assert server.port == 22
        assert server.user is None
        assert server.key is None
        assert server.connection_options == SshConnectionOptions()
        assert server.connection_options.connect_timeout == 10

    def test_construction_full(self) -> None:
        server = SshEndpoint(
            slug="nas-server",
            host="nas.local",
            port=2222,
            user="backup",
            key="~/.ssh/id_rsa",
            connection_options=SshConnectionOptions(connect_timeout=30),
        )
        assert server.port == 2222
        assert server.user == "backup"
        assert server.key == str(Path("~/.ssh/id_rsa").expanduser())
        assert server.connection_options.connect_timeout == 30

    def test_construction_with_proxy_jump(self) -> None:
        server = SshEndpoint(
            slug="target",
            host="target.internal",
            proxy_jump="bastion",
        )
        assert server.proxy_jump == "bastion"

    def test_proxy_jump_defaults_to_none(self) -> None:
        server = SshEndpoint(slug="nas-server", host="nas.local")
        assert server.proxy_jump is None


class TestRemoteVolume:
    def test_construction(self) -> None:
        vol = RemoteVolume(
            slug="nas",
            ssh_endpoint="nas-server",
            path="/backup",
        )
        assert vol.slug == "nas"
        assert vol.ssh_endpoint == "nas-server"
        assert vol.path == "/backup"

    def test_frozen(self) -> None:
        import pydantic

        vol = RemoteVolume(
            slug="nas",
            ssh_endpoint="nas-server",
            path="/backup",
        )
        try:
            vol.path = "other"  # type: ignore[misc]
            assert False, "Should be frozen"
        except (AttributeError, pydantic.ValidationError):
            pass


class TestSyncEndpoint:
    def test_construction_defaults(self) -> None:
        ep = SyncEndpoint(slug="ep-data", volume="data")
        assert ep.slug == "ep-data"
        assert ep.volume == "data"
        assert ep.subdir is None

    def test_construction_with_subdir(self) -> None:
        ep = SyncEndpoint(slug="ep-data", volume="data", subdir="photos")
        assert ep.subdir == "photos"


class TestSyncConfig:
    def test_construction_defaults(self) -> None:
        sc = SyncConfig(
            slug="sync1",
            source="ep-src",
            destination="ep-dst",
        )
        assert sc.slug == "sync1"
        assert sc.source == "ep-src"
        assert sc.destination == "ep-dst"
        assert sc.enabled is True
        assert sc.rsync_options.default_options_override is None
        assert sc.rsync_options.extra_options == []
        assert sc.rsync_options.checksum is True
        assert sc.rsync_options.compress is False
        assert sc.filters == []
        assert sc.filter_file is None

    def test_construction_full(self) -> None:
        sc = SyncConfig(
            slug="sync1",
            source="ep-src",
            destination="ep-dst",
            enabled=False,
            rsync_options=RsyncOptions(
                default_options_override=["-a", "--delete"],
                extra_options=["--bwlimit=1000"],
                compress=True,
            ),
            filters=["+ *.jpg", "- *.tmp"],
            filter_file="/etc/nbkp/filters.rules",
        )
        assert sc.enabled is False
        assert sc.rsync_options.default_options_override == [
            "-a",
            "--delete",
        ]
        assert sc.rsync_options.extra_options == ["--bwlimit=1000"]
        assert sc.rsync_options.compress is True
        assert sc.rsync_options.checksum is True
        assert sc.filters == ["+ *.jpg", "- *.tmp"]
        assert sc.filter_file == "/etc/nbkp/filters.rules"


class TestConfig:
    def test_empty(self) -> None:
        cfg = Config()
        assert cfg.volumes == {}
        assert cfg.syncs == {}

    def test_with_data(self) -> None:
        vol = LocalVolume(slug="data", path="/mnt/data")
        cfg = Config(
            volumes={"data": vol},
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="data"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="data", subdir="sub"),
            },
            syncs={
                "s1": SyncConfig(
                    slug="s1",
                    source="ep-src",
                    destination="ep-dst",
                ),
            },
        )
        assert "data" in cfg.volumes
        assert "s1" in cfg.syncs


class TestCrossServerValidation:
    def test_cross_server_remote_to_remote_rejected(self) -> None:
        import pydantic
        import pytest

        with pytest.raises(pydantic.ValidationError):
            Config(
                ssh_endpoints={
                    "a": SshEndpoint(slug="a", host="a.com"),
                    "b": SshEndpoint(slug="b", host="b.com"),
                },
                volumes={
                    "src": RemoteVolume(
                        slug="src",
                        ssh_endpoint="a",
                        path="/s",
                    ),
                    "dst": RemoteVolume(
                        slug="dst",
                        ssh_endpoint="b",
                        path="/d",
                    ),
                },
                sync_endpoints={
                    "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                    "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
                },
                syncs={
                    "x": SyncConfig(
                        slug="x",
                        source="ep-src",
                        destination="ep-dst",
                    )
                },
            )

    def test_same_server_remote_to_remote_allowed(self) -> None:
        config = Config(
            ssh_endpoints={
                "server": SshEndpoint(slug="server", host="server.com"),
            },
            volumes={
                "src": RemoteVolume(
                    slug="src",
                    ssh_endpoint="server",
                    path="/src",
                ),
                "dst": RemoteVolume(
                    slug="dst",
                    ssh_endpoint="server",
                    path="/dst",
                ),
            },
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={
                "x": SyncConfig(
                    slug="x",
                    source="ep-src",
                    destination="ep-dst",
                )
            },
        )
        assert "x" in config.syncs


class TestVolumeStatus:
    def test_construction_active(self) -> None:
        vol = LocalVolume(slug="data", path="/mnt/data")
        vs = VolumeStatus(
            slug="data",
            config=vol,
            reasons=[],
        )
        assert vs.active is True

    def test_construction_inactive(self) -> None:
        vol = LocalVolume(slug="data", path="/mnt/data")
        vs = VolumeStatus(
            slug="data",
            config=vol,
            reasons=[VolumeReason.SENTINEL_NOT_FOUND],
        )
        assert vs.active is False


class TestSyncStatus:
    def test_construction_active(self) -> None:
        vol = LocalVolume(slug="data", path="/mnt/data")
        vs = VolumeStatus(
            slug="data",
            config=vol,
            reasons=[],
        )
        sc = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
        )
        ss = SyncStatus(
            slug="s1",
            config=sc,
            source_status=vs,
            destination_status=vs,
            reasons=[],
        )
        assert ss.active is True

    def test_construction_inactive(self) -> None:
        vol = LocalVolume(slug="data", path="/mnt/data")
        vs = VolumeStatus(
            slug="data",
            config=vol,
            reasons=[],
        )
        sc = SyncConfig(
            slug="s1",
            source="ep-src",
            destination="ep-dst",
        )
        ss = SyncStatus(
            slug="s1",
            config=sc,
            source_status=vs,
            destination_status=vs,
            reasons=[SyncReason.DISABLED],
        )
        assert ss.active is False


class TestSyncResult:
    def test_construction_defaults(self) -> None:
        sr = SyncResult(
            sync_slug="s1",
            success=True,
            dry_run=False,
            rsync_exit_code=0,
            output="done",
        )
        assert sr.snapshot_path is None
        assert sr.detail is None

    def test_construction_full(self) -> None:
        sr = SyncResult(
            sync_slug="s1",
            success=False,
            dry_run=False,
            rsync_exit_code=1,
            output="",
            detail="failed",
            snapshot_path="/snap/2024",
        )
        assert sr.detail == "failed"
        assert sr.snapshot_path == "/snap/2024"


class TestSlugValidation:
    def test_valid_simple(self) -> None:
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert vol.slug == "data"

    def test_valid_kebab_case(self) -> None:
        vol = LocalVolume(slug="my-usb-drive", path="/mnt")
        assert vol.slug == "my-usb-drive"

    def test_valid_with_numbers(self) -> None:
        vol = LocalVolume(slug="nas2", path="/mnt")
        assert vol.slug == "nas2"

    def test_invalid_uppercase(self) -> None:
        import pytest

        with pytest.raises(Exception):
            LocalVolume(slug="MyDrive", path="/mnt")

    def test_invalid_underscore(self) -> None:
        import pytest

        with pytest.raises(Exception):
            LocalVolume(slug="my_drive", path="/mnt")

    def test_invalid_spaces(self) -> None:
        import pytest

        with pytest.raises(Exception):
            LocalVolume(slug="my drive", path="/mnt")

    def test_invalid_trailing_hyphen(self) -> None:
        import pytest

        with pytest.raises(Exception):
            LocalVolume(slug="drive-", path="/mnt")

    def test_invalid_leading_hyphen(self) -> None:
        import pytest

        with pytest.raises(Exception):
            LocalVolume(slug="-drive", path="/mnt")

    def test_invalid_empty(self) -> None:
        import pytest

        with pytest.raises(Exception):
            LocalVolume(slug="", path="/mnt")

    def test_invalid_too_long(self) -> None:
        import pytest

        with pytest.raises(Exception):
            LocalVolume(slug="a" * 51, path="/mnt")

    def test_valid_max_length(self) -> None:
        vol = LocalVolume(slug="a" * 50, path="/mnt")
        assert len(vol.slug) == 50


class TestOutputFormat:
    def test_values(self) -> None:
        assert OutputFormat.HUMAN.value == "human"
        assert OutputFormat.JSON.value == "json"


# --- Check function tests (moved from test_checks.py) ---


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
    """Build resolved endpoints from config for testing."""
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
        status = check_volume(vol)
        assert status.active is True
        assert status.reasons == []

    def test_inactive(self, tmp_path: Path) -> None:
        vol = LocalVolume(slug="data", path=str(tmp_path))
        status = check_volume(vol)
        assert status.active is False
        assert status.reasons == [VolumeReason.SENTINEL_NOT_FOUND]


class TestCheckRemoteVolume:
    @patch("nbkp.preflight.checks.run_remote_command")
    def test_active(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        vol, config = _remote_config()
        resolved = _make_resolved(config)
        status = check_volume(vol, resolved)
        assert status.active is True
        assert status.reasons == []
        server = config.ssh_endpoints["nas-server"]
        mock_run.assert_called_once_with(
            server, ["test", "-f", "/backup/.nbkp-vol"], []
        )

    @patch("nbkp.preflight.checks.run_remote_command")
    def test_inactive(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1)
        vol, config = _remote_config()
        resolved = _make_resolved(config)
        status = check_volume(vol, resolved)
        assert status.active is False
        assert status.reasons == [VolumeReason.UNREACHABLE]


class TestCheckRemoteVolumeLocationExcluded:
    def test_excluded_volume_no_ssh(self) -> None:
        """Volume excluded by location is marked without SSH."""
        vol, _config = _remote_config()
        # Empty resolved_endpoints means volume was excluded
        status = check_volume(vol, {})
        assert status.active is False
        assert status.reasons == [VolumeReason.LOCATION_EXCLUDED]


class TestCheckCommandAvailableLocal:
    @patch("nbkp.preflight.checks.shutil.which")
    def test_command_found(self, mock_which: MagicMock) -> None:
        mock_which.return_value = "/usr/bin/rsync"
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert _check_command_available(vol, "rsync", {}) is True
        mock_which.assert_called_once_with("rsync")

    @patch("nbkp.preflight.checks.shutil.which")
    def test_command_not_found(self, mock_which: MagicMock) -> None:
        mock_which.return_value = None
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert _check_command_available(vol, "rsync", {}) is False
        mock_which.assert_called_once_with("rsync")


class TestCheckCommandAvailableRemote:
    @patch("nbkp.preflight.checks.run_remote_command")
    def test_command_found(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        vol, config = _remote_config()
        resolved = _make_resolved(config)
        assert _check_command_available(vol, "rsync", resolved) is True
        server = config.ssh_endpoints["nas-server"]
        mock_run.assert_called_once_with(server, ["which", "rsync"], [])

    @patch("nbkp.preflight.checks.run_remote_command")
    def test_command_not_found(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1)
        vol, config = _remote_config()
        resolved = _make_resolved(config)
        assert _check_command_available(vol, "btrfs", resolved) is False
        server = config.ssh_endpoints["nas-server"]
        mock_run.assert_called_once_with(server, ["which", "btrfs"], [])


class TestCheckBtrfsFilesystemLocal:
    @patch("nbkp.preflight.checks.subprocess.run")
    def test_btrfs(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="btrfs\n")
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert _check_btrfs_filesystem(vol, {}) is True
        mock_run.assert_called_once_with(
            ["stat", "-f", "-c", "%T", "/mnt/data"],
            capture_output=True,
            text=True,
        )

    @patch("nbkp.preflight.checks.subprocess.run")
    def test_not_btrfs(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="ext2/ext3\n")
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert _check_btrfs_filesystem(vol, {}) is False

    @patch("nbkp.preflight.checks.subprocess.run")
    def test_stat_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert _check_btrfs_filesystem(vol, {}) is False


class TestCheckBtrfsFilesystemRemote:
    @patch("nbkp.preflight.checks.run_remote_command")
    def test_btrfs(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="btrfs\n")
        vol, config = _remote_config()
        resolved = _make_resolved(config)
        assert _check_btrfs_filesystem(vol, resolved) is True
        server = config.ssh_endpoints["nas-server"]
        mock_run.assert_called_once_with(
            server,
            ["stat", "-f", "-c", "%T", "/backup"],
            [],
        )

    @patch("nbkp.preflight.checks.run_remote_command")
    def test_not_btrfs(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="ext2/ext3\n")
        vol, config = _remote_config()
        resolved = _make_resolved(config)
        assert _check_btrfs_filesystem(vol, resolved) is False


class TestCheckBtrfsSubvolumeLocal:
    @patch("nbkp.preflight.checks.subprocess.run")
    def test_is_subvolume(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="256\n")
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert _check_btrfs_subvolume(vol, None, {}) is True
        mock_run.assert_called_once_with(
            ["stat", "-c", "%i", "/mnt/data"],
            capture_output=True,
            text=True,
        )

    @patch("nbkp.preflight.checks.subprocess.run")
    def test_is_subvolume_with_subdir(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="256\n")
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert _check_btrfs_subvolume(vol, "backup", {}) is True
        mock_run.assert_called_once_with(
            ["stat", "-c", "%i", "/mnt/data/backup"],
            capture_output=True,
            text=True,
        )

    @patch("nbkp.preflight.checks.subprocess.run")
    def test_not_subvolume(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="1234\n")
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert _check_btrfs_subvolume(vol, None, {}) is False

    @patch("nbkp.preflight.checks.subprocess.run")
    def test_stat_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert _check_btrfs_subvolume(vol, None, {}) is False


class TestCheckBtrfsSubvolumeRemote:
    @patch("nbkp.preflight.checks.run_remote_command")
    def test_is_subvolume(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="256\n")
        vol, config = _remote_config()
        resolved = _make_resolved(config)
        assert _check_btrfs_subvolume(vol, None, resolved) is True
        server = config.ssh_endpoints["nas-server"]
        mock_run.assert_called_once_with(
            server,
            ["stat", "-c", "%i", "/backup"],
            [],
        )

    @patch("nbkp.preflight.checks.run_remote_command")
    def test_is_subvolume_with_subdir(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="256\n")
        vol, config = _remote_config()
        resolved = _make_resolved(config)
        assert _check_btrfs_subvolume(vol, "data", resolved) is True
        server = config.ssh_endpoints["nas-server"]
        mock_run.assert_called_once_with(
            server,
            ["stat", "-c", "%i", "/backup/data"],
            [],
        )

    @patch("nbkp.preflight.checks.run_remote_command")
    def test_not_subvolume(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="1234\n")
        vol, config = _remote_config()
        resolved = _make_resolved(config)
        assert _check_btrfs_subvolume(vol, None, resolved) is False


class TestCheckBtrfsMountOptionLocal:
    @patch("nbkp.preflight.checks.subprocess.run")
    def test_option_present(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="rw,relatime,user_subvol_rm_allowed\n",
        )
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert _check_btrfs_mount_option(vol, "user_subvol_rm_allowed", {}) is True
        mock_run.assert_called_once_with(
            ["findmnt", "-T", "/mnt/data", "-n", "-o", "OPTIONS"],
            capture_output=True,
            text=True,
        )

    @patch("nbkp.preflight.checks.subprocess.run")
    def test_option_missing(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="rw,relatime\n")
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert _check_btrfs_mount_option(vol, "user_subvol_rm_allowed", {}) is False

    @patch("nbkp.preflight.checks.subprocess.run")
    def test_findmnt_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert _check_btrfs_mount_option(vol, "user_subvol_rm_allowed", {}) is False


class TestCheckBtrfsMountOptionRemote:
    @patch("nbkp.preflight.checks.run_remote_command")
    def test_option_present(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="rw,relatime,user_subvol_rm_allowed\n",
        )
        vol, config = _remote_config()
        resolved = _make_resolved(config)
        assert (
            _check_btrfs_mount_option(vol, "user_subvol_rm_allowed", resolved) is True
        )
        server = config.ssh_endpoints["nas-server"]
        mock_run.assert_called_once_with(
            server,
            ["findmnt", "-T", "/backup", "-n", "-o", "OPTIONS"],
            [],
        )

    @patch("nbkp.preflight.checks.run_remote_command")
    def test_option_missing(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="rw,relatime\n")
        vol, config = _remote_config()
        resolved = _make_resolved(config)
        assert (
            _check_btrfs_mount_option(vol, "user_subvol_rm_allowed", resolved) is False
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

    @patch("nbkp.preflight.checks._check_rsync_version", return_value=True)
    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/rsync",
    )
    def test_active_sync(
        self,
        mock_which: MagicMock,
        _mock_rsync_ver: MagicMock,
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
        vol_statuses = {
            "src": VolumeStatus(
                slug="src",
                config=config.volumes["src"],
                reasons=[],
            ),
            "dst": VolumeStatus(
                slug="dst",
                config=config.volumes["dst"],
                reasons=[],
            ),
        }

        status = check_sync(sync, config, vol_statuses)
        assert status.active is True
        assert status.reasons == []

    def test_disabled_sync(self, tmp_path: Path) -> None:
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
        vol_statuses = {
            "src": VolumeStatus(
                slug="src",
                config=config.volumes["src"],
                reasons=[],
            ),
            "dst": VolumeStatus(
                slug="dst",
                config=config.volumes["dst"],
                reasons=[],
            ),
        }

        status = check_sync(sync, config, vol_statuses)
        assert status.active is False
        assert status.reasons == [SyncReason.DISABLED]

    def test_source_unavailable(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()

        config, sync = self._make_config(src, dst)
        vol_statuses = {
            "src": VolumeStatus(
                slug="src",
                config=config.volumes["src"],
                reasons=[VolumeReason.SENTINEL_NOT_FOUND],
            ),
            "dst": VolumeStatus(
                slug="dst",
                config=config.volumes["dst"],
                reasons=[],
            ),
        }

        status = check_sync(sync, config, vol_statuses)
        assert status.active is False
        assert SyncReason.SOURCE_UNAVAILABLE in status.reasons

    def test_missing_src_sentinel(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        (src / "data").mkdir()
        (dst / "backup").mkdir()
        (dst / "backup" / ".nbkp-dst").touch()

        config, sync = self._make_config(src, dst)
        vol_statuses = {
            "src": VolumeStatus(
                slug="src",
                config=config.volumes["src"],
                reasons=[],
            ),
            "dst": VolumeStatus(
                slug="dst",
                config=config.volumes["dst"],
                reasons=[],
            ),
        }

        status = check_sync(sync, config, vol_statuses)
        assert status.active is False
        assert SyncReason.SOURCE_SENTINEL_NOT_FOUND in status.reasons

    def _setup_active_sentinels(self, src: Path, dst: Path) -> None:
        (src / ".nbkp-vol").touch()
        (dst / ".nbkp-vol").touch()
        (src / "data").mkdir(exist_ok=True)
        (src / "data" / ".nbkp-src").touch()
        (dst / "backup").mkdir(exist_ok=True)
        (dst / "backup" / ".nbkp-dst").touch()

    def _make_active_vol_statuses(self, config: Config) -> dict[str, VolumeStatus]:
        return {
            "src": VolumeStatus(
                slug="src",
                config=config.volumes["src"],
                reasons=[],
            ),
            "dst": VolumeStatus(
                slug="dst",
                config=config.volumes["dst"],
                reasons=[],
            ),
        }

    @patch("nbkp.preflight.checks.shutil.which", return_value=None)
    def test_rsync_not_found_on_source(
        self, mock_which: MagicMock, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_active_sentinels(src, dst)

        config, sync = self._make_config(src, dst)
        vol_statuses = self._make_active_vol_statuses(config)

        status = check_sync(sync, config, vol_statuses)
        assert status.active is False
        assert SyncReason.RSYNC_NOT_FOUND_ON_SOURCE in status.reasons
        assert SyncReason.RSYNC_NOT_FOUND_ON_DESTINATION in status.reasons

    @patch(
        "nbkp.preflight.checks.shutil.which",
        side_effect=lambda cmd: None if cmd == "btrfs" else f"/usr/bin/{cmd}",
    )
    def test_rsync_found_btrfs_not_found_on_destination(
        self, mock_which: MagicMock, tmp_path: Path
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
        vol_statuses = self._make_active_vol_statuses(config)

        status = check_sync(sync, config, vol_statuses)
        assert status.active is False
        assert SyncReason.BTRFS_NOT_FOUND_ON_DESTINATION in status.reasons

    @patch(
        "nbkp.preflight.checks.shutil.which",
        side_effect=lambda cmd: None if cmd == "stat" else f"/usr/bin/{cmd}",
    )
    def test_stat_not_found_on_destination(
        self, mock_which: MagicMock, tmp_path: Path
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
        vol_statuses = self._make_active_vol_statuses(config)

        status = check_sync(sync, config, vol_statuses)
        assert status.active is False
        assert SyncReason.STAT_NOT_FOUND_ON_DESTINATION in status.reasons
        assert SyncReason.DESTINATION_NOT_BTRFS not in status.reasons
        assert SyncReason.DESTINATION_NOT_BTRFS_SUBVOLUME not in status.reasons

    @patch("nbkp.preflight.checks._check_rsync_version", return_value=True)
    @patch("nbkp.preflight.checks.subprocess.run")
    @patch(
        "nbkp.preflight.checks.shutil.which",
        side_effect=lambda cmd: None if cmd == "findmnt" else f"/usr/bin/{cmd}",
    )
    def test_findmnt_not_found_on_destination(
        self,
        mock_which: MagicMock,
        mock_subprocess: MagicMock,
        _mock_rsync_ver: MagicMock,
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
        vol_statuses = self._make_active_vol_statuses(config)

        def subprocess_side_effect(cmd: list[str], **kwargs: object) -> MagicMock:
            if cmd[:4] == ["stat", "-f", "-c", "%T"]:
                return MagicMock(returncode=0, stdout="btrfs\n")
            if cmd[:3] == ["stat", "-c", "%i"]:
                return MagicMock(returncode=0, stdout="256\n")
            return MagicMock(returncode=0)

        mock_subprocess.side_effect = subprocess_side_effect

        status = check_sync(sync, config, vol_statuses)
        assert status.active is False
        assert SyncReason.FINDMNT_NOT_FOUND_ON_DESTINATION in status.reasons
        assert SyncReason.DESTINATION_NOT_MOUNTED_USER_SUBVOL_RM not in status.reasons

    @patch(
        "nbkp.preflight.checks.shutil.which",
        side_effect=lambda cmd: (
            None if cmd in ("stat", "findmnt") else f"/usr/bin/{cmd}"
        ),
    )
    def test_stat_and_findmnt_both_missing(
        self, mock_which: MagicMock, tmp_path: Path
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
        vol_statuses = self._make_active_vol_statuses(config)

        status = check_sync(sync, config, vol_statuses)
        assert status.active is False
        assert SyncReason.STAT_NOT_FOUND_ON_DESTINATION in status.reasons
        assert SyncReason.FINDMNT_NOT_FOUND_ON_DESTINATION in status.reasons

    @patch("nbkp.preflight.checks.subprocess.run")
    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/fake",
    )
    def test_destination_not_btrfs_filesystem(
        self,
        mock_which: MagicMock,
        mock_subprocess: MagicMock,
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
        vol_statuses = self._make_active_vol_statuses(config)

        mock_subprocess.return_value = MagicMock(returncode=0, stdout="ext2/ext3\n")

        status = check_sync(sync, config, vol_statuses)
        assert status.active is False
        assert SyncReason.DESTINATION_NOT_BTRFS in status.reasons

    @patch("nbkp.preflight.checks._check_rsync_version", return_value=True)
    @patch("nbkp.preflight.checks.subprocess.run")
    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/fake",
    )
    def test_destination_not_btrfs_subvolume(
        self,
        mock_which: MagicMock,
        mock_subprocess: MagicMock,
        _mock_rsync_ver: MagicMock,
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
        vol_statuses = self._make_active_vol_statuses(config)

        def subprocess_side_effect(cmd: list[str], **kwargs: object) -> MagicMock:
            if cmd[:4] == ["stat", "-f", "-c", "%T"]:
                return MagicMock(returncode=0, stdout="btrfs\n")
            if cmd[:3] == ["stat", "-c", "%i"]:
                return MagicMock(returncode=0, stdout="1234\n")
            return MagicMock(returncode=0)

        mock_subprocess.side_effect = subprocess_side_effect

        status = check_sync(sync, config, vol_statuses)
        assert status.active is False
        assert SyncReason.DESTINATION_NOT_BTRFS_SUBVOLUME in status.reasons

    @patch("nbkp.preflight.checks._check_rsync_version", return_value=True)
    @patch("nbkp.preflight.checks.subprocess.run")
    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/fake",
    )
    def test_destination_not_mounted_user_subvol_rm(
        self,
        mock_which: MagicMock,
        mock_subprocess: MagicMock,
        _mock_rsync_ver: MagicMock,
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
        vol_statuses = self._make_active_vol_statuses(config)

        def subprocess_side_effect(cmd: list[str], **kwargs: object) -> MagicMock:
            if cmd[:4] == ["stat", "-f", "-c", "%T"]:
                return MagicMock(returncode=0, stdout="btrfs\n")
            if cmd[:3] == ["stat", "-c", "%i"]:
                return MagicMock(returncode=0, stdout="256\n")
            if cmd[0] == "findmnt":
                return MagicMock(returncode=0, stdout="rw,relatime\n")
            return MagicMock(returncode=0)

        mock_subprocess.side_effect = subprocess_side_effect

        status = check_sync(sync, config, vol_statuses)
        assert status.active is False
        assert SyncReason.DESTINATION_NOT_MOUNTED_USER_SUBVOL_RM in status.reasons

    @patch("nbkp.preflight.checks._check_rsync_version", return_value=True)
    @patch("nbkp.preflight.checks.subprocess.run")
    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/fake",
    )
    def test_destination_latest_not_found(
        self,
        mock_which: MagicMock,
        mock_subprocess: MagicMock,
        _mock_rsync_ver: MagicMock,
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
        vol_statuses = self._make_active_vol_statuses(config)

        def subprocess_side_effect(cmd: list[str], **kwargs: object) -> MagicMock:
            if cmd[:4] == ["stat", "-f", "-c", "%T"]:
                return MagicMock(returncode=0, stdout="btrfs\n")
            if cmd[:3] == ["stat", "-c", "%i"]:
                return MagicMock(returncode=0, stdout="256\n")
            if cmd[0] == "findmnt":
                return MagicMock(
                    returncode=0,
                    stdout="rw,user_subvol_rm_allowed\n",
                )
            return MagicMock(returncode=0)

        mock_subprocess.side_effect = subprocess_side_effect

        status = check_sync(sync, config, vol_statuses)
        assert status.active is False
        assert SyncReason.DESTINATION_TMP_NOT_FOUND in status.reasons
        assert SyncReason.DESTINATION_SNAPSHOTS_DIR_NOT_FOUND not in status.reasons

    @patch("nbkp.preflight.checks._check_rsync_version", return_value=True)
    @patch("nbkp.preflight.checks.subprocess.run")
    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/fake",
    )
    def test_destination_snapshots_dir_not_found(
        self,
        mock_which: MagicMock,
        mock_subprocess: MagicMock,
        _mock_rsync_ver: MagicMock,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_active_sentinels(src, dst)
        # tmp exists but snapshots does not
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
        vol_statuses = self._make_active_vol_statuses(config)

        def subprocess_side_effect(cmd: list[str], **kwargs: object) -> MagicMock:
            if cmd[:4] == ["stat", "-f", "-c", "%T"]:
                return MagicMock(returncode=0, stdout="btrfs\n")
            if cmd[:3] == ["stat", "-c", "%i"]:
                return MagicMock(returncode=0, stdout="256\n")
            if cmd[0] == "findmnt":
                return MagicMock(
                    returncode=0,
                    stdout="rw,user_subvol_rm_allowed\n",
                )
            return MagicMock(returncode=0)

        mock_subprocess.side_effect = subprocess_side_effect

        status = check_sync(sync, config, vol_statuses)
        assert status.active is False
        assert SyncReason.DESTINATION_SNAPSHOTS_DIR_NOT_FOUND in status.reasons
        assert SyncReason.DESTINATION_TMP_NOT_FOUND not in status.reasons

    @patch("nbkp.preflight.checks._check_rsync_version", return_value=True)
    @patch("nbkp.preflight.checks.subprocess.run")
    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/fake",
    )
    def test_destination_latest_and_snapshots_both_missing(
        self,
        mock_which: MagicMock,
        mock_subprocess: MagicMock,
        _mock_rsync_ver: MagicMock,
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
        vol_statuses = self._make_active_vol_statuses(config)

        def subprocess_side_effect(cmd: list[str], **kwargs: object) -> MagicMock:
            if cmd[:4] == ["stat", "-f", "-c", "%T"]:
                return MagicMock(returncode=0, stdout="btrfs\n")
            if cmd[:3] == ["stat", "-c", "%i"]:
                return MagicMock(returncode=0, stdout="256\n")
            if cmd[0] == "findmnt":
                return MagicMock(
                    returncode=0,
                    stdout="rw,user_subvol_rm_allowed\n",
                )
            return MagicMock(returncode=0)

        mock_subprocess.side_effect = subprocess_side_effect

        status = check_sync(sync, config, vol_statuses)
        assert status.active is False
        assert SyncReason.DESTINATION_TMP_NOT_FOUND in status.reasons
        assert SyncReason.DESTINATION_SNAPSHOTS_DIR_NOT_FOUND in status.reasons

    @patch("nbkp.preflight.checks._check_rsync_version", return_value=True)
    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/rsync",
    )
    def test_btrfs_check_skipped_when_not_enabled(
        self,
        mock_which: MagicMock,
        _mock_rsync_ver: MagicMock,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_active_sentinels(src, dst)

        config, sync = self._make_config(src, dst)
        vol_statuses = self._make_active_vol_statuses(config)

        status = check_sync(sync, config, vol_statuses)
        assert status.active is True
        assert status.reasons == []

    @patch("nbkp.preflight.checks.shutil.which", return_value=None)
    def test_multiple_failures_accumulated(
        self, mock_which: MagicMock, tmp_path: Path
    ) -> None:
        """Source sentinel missing AND rsync missing on both sides."""
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
        vol_statuses = self._make_active_vol_statuses(config)

        status = check_sync(sync, config, vol_statuses)
        assert status.active is False
        assert SyncReason.SOURCE_SENTINEL_NOT_FOUND in status.reasons
        assert SyncReason.RSYNC_NOT_FOUND_ON_SOURCE in status.reasons
        assert SyncReason.RSYNC_NOT_FOUND_ON_DESTINATION in status.reasons

    def test_both_volumes_unavailable(self, tmp_path: Path) -> None:
        """Both source and destination unavailable."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()

        config, sync = self._make_config(src, dst)
        vol_statuses = {
            "src": VolumeStatus(
                slug="src",
                config=config.volumes["src"],
                reasons=[VolumeReason.SENTINEL_NOT_FOUND],
            ),
            "dst": VolumeStatus(
                slug="dst",
                config=config.volumes["dst"],
                reasons=[VolumeReason.SENTINEL_NOT_FOUND],
            ),
        }

        status = check_sync(sync, config, vol_statuses)
        assert status.active is False
        assert SyncReason.SOURCE_UNAVAILABLE in status.reasons
        assert SyncReason.DESTINATION_UNAVAILABLE in status.reasons


class TestCheckSyncRemoteCommands:
    @patch("nbkp.preflight.checks.run_remote_command")
    def test_rsync_not_found_on_remote_source(
        self, mock_run: MagicMock, tmp_path: Path
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
        vol_statuses = {
            "src": VolumeStatus(
                slug="src",
                config=src_vol,
                reasons=[],
            ),
            "dst": VolumeStatus(
                slug="dst",
                config=dst_vol,
                reasons=[],
            ),
        }

        def remote_side_effect(
            server: SshEndpoint,
            cmd: list[str],
            proxy_chain: list[SshEndpoint] | None = None,
        ) -> MagicMock:
            if cmd == ["test", "-f", "/data/data/.nbkp-src"]:
                return MagicMock(returncode=0)
            if cmd == ["which", "rsync"]:
                return MagicMock(returncode=1)
            return MagicMock(returncode=0)

        mock_run.side_effect = remote_side_effect

        status = check_sync(sync, config, vol_statuses, _make_resolved(config))
        assert status.active is False
        assert SyncReason.RSYNC_NOT_FOUND_ON_SOURCE in status.reasons

    @patch("nbkp.preflight.checks.run_remote_command")
    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/rsync",
    )
    def test_rsync_not_found_on_remote_destination(
        self,
        mock_which: MagicMock,
        mock_run: MagicMock,
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
        vol_statuses = {
            "src": VolumeStatus(
                slug="src",
                config=src_vol,
                reasons=[],
            ),
            "dst": VolumeStatus(
                slug="dst",
                config=dst_vol,
                reasons=[],
            ),
        }

        def remote_side_effect(
            server: SshEndpoint,
            cmd: list[str],
            proxy_chain: list[SshEndpoint] | None = None,
        ) -> MagicMock:
            if cmd == [
                "test",
                "-f",
                "/backup/backup/.nbkp-dst",
            ]:
                return MagicMock(returncode=0)
            if cmd == ["which", "rsync"]:
                return MagicMock(returncode=1)
            return MagicMock(returncode=0)

        mock_run.side_effect = remote_side_effect

        status = check_sync(sync, config, vol_statuses, _make_resolved(config))
        assert status.active is False
        assert SyncReason.RSYNC_NOT_FOUND_ON_DESTINATION in status.reasons

    @patch("nbkp.preflight.checks._check_rsync_version", return_value=True)
    @patch("nbkp.preflight.checks.run_remote_command")
    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/rsync",
    )
    def test_btrfs_not_found_on_remote_destination(
        self,
        mock_which: MagicMock,
        mock_run: MagicMock,
        _mock_rsync_ver: MagicMock,
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
        vol_statuses = {
            "src": VolumeStatus(
                slug="src",
                config=src_vol,
                reasons=[],
            ),
            "dst": VolumeStatus(
                slug="dst",
                config=dst_vol,
                reasons=[],
            ),
        }

        def remote_side_effect(
            server: SshEndpoint,
            cmd: list[str],
            proxy_chain: list[SshEndpoint] | None = None,
        ) -> MagicMock:
            if cmd == [
                "test",
                "-f",
                "/backup/backup/.nbkp-dst",
            ]:
                return MagicMock(returncode=0)
            if cmd == ["which", "rsync"]:
                return MagicMock(returncode=0)
            if cmd == ["which", "btrfs"]:
                return MagicMock(returncode=1)
            return MagicMock(returncode=0)

        mock_run.side_effect = remote_side_effect

        status = check_sync(sync, config, vol_statuses, _make_resolved(config))
        assert status.active is False
        assert SyncReason.BTRFS_NOT_FOUND_ON_DESTINATION in status.reasons

    @patch("nbkp.preflight.checks._check_rsync_version", return_value=True)
    @patch("nbkp.preflight.checks.run_remote_command")
    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/rsync",
    )
    def test_destination_not_btrfs_on_remote(
        self,
        mock_which: MagicMock,
        mock_run: MagicMock,
        _mock_rsync_ver: MagicMock,
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
        vol_statuses = {
            "src": VolumeStatus(
                slug="src",
                config=src_vol,
                reasons=[],
            ),
            "dst": VolumeStatus(
                slug="dst",
                config=dst_vol,
                reasons=[],
            ),
        }

        def remote_side_effect(
            server: SshEndpoint,
            cmd: list[str],
            proxy_chain: list[SshEndpoint] | None = None,
        ) -> MagicMock:
            if cmd == [
                "test",
                "-f",
                "/backup/backup/.nbkp-dst",
            ]:
                return MagicMock(returncode=0)
            if cmd == ["which", "rsync"]:
                return MagicMock(returncode=0)
            if cmd == ["which", "btrfs"]:
                return MagicMock(returncode=0)
            if cmd == [
                "stat",
                "-f",
                "-c",
                "%T",
                "/backup",
            ]:
                return MagicMock(returncode=0, stdout="ext2/ext3\n")
            return MagicMock(returncode=0)

        mock_run.side_effect = remote_side_effect

        status = check_sync(sync, config, vol_statuses, _make_resolved(config))
        assert status.active is False
        assert SyncReason.DESTINATION_NOT_BTRFS in status.reasons

    @patch("nbkp.preflight.checks._check_rsync_version", return_value=True)
    @patch("nbkp.preflight.checks.run_remote_command")
    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/rsync",
    )
    def test_destination_not_subvolume_on_remote(
        self,
        mock_which: MagicMock,
        mock_run: MagicMock,
        _mock_rsync_ver: MagicMock,
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
        vol_statuses = {
            "src": VolumeStatus(
                slug="src",
                config=src_vol,
                reasons=[],
            ),
            "dst": VolumeStatus(
                slug="dst",
                config=dst_vol,
                reasons=[],
            ),
        }

        def remote_side_effect(
            server: SshEndpoint,
            cmd: list[str],
            proxy_chain: list[SshEndpoint] | None = None,
        ) -> MagicMock:
            if cmd == [
                "test",
                "-f",
                "/backup/backup/.nbkp-dst",
            ]:
                return MagicMock(returncode=0)
            if cmd == ["which", "rsync"]:
                return MagicMock(returncode=0)
            if cmd == ["which", "btrfs"]:
                return MagicMock(returncode=0)
            if cmd == [
                "stat",
                "-f",
                "-c",
                "%T",
                "/backup",
            ]:
                return MagicMock(returncode=0, stdout="btrfs\n")
            if cmd == [
                "stat",
                "-c",
                "%i",
                "/backup/backup",
            ]:
                return MagicMock(returncode=0, stdout="1234\n")
            return MagicMock(returncode=0)

        mock_run.side_effect = remote_side_effect

        status = check_sync(sync, config, vol_statuses, _make_resolved(config))
        assert status.active is False
        assert SyncReason.DESTINATION_NOT_BTRFS_SUBVOLUME in status.reasons

    @patch("nbkp.preflight.checks._check_rsync_version", return_value=True)
    @patch("nbkp.preflight.checks.run_remote_command")
    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/rsync",
    )
    def test_destination_not_mounted_user_subvol_rm_on_remote(
        self,
        mock_which: MagicMock,
        mock_run: MagicMock,
        _mock_rsync_ver: MagicMock,
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
        vol_statuses = {
            "src": VolumeStatus(
                slug="src",
                config=src_vol,
                reasons=[],
            ),
            "dst": VolumeStatus(
                slug="dst",
                config=dst_vol,
                reasons=[],
            ),
        }

        def remote_side_effect(
            server: SshEndpoint,
            cmd: list[str],
            proxy_chain: list[SshEndpoint] | None = None,
        ) -> MagicMock:
            if cmd == [
                "test",
                "-f",
                "/backup/backup/.nbkp-dst",
            ]:
                return MagicMock(returncode=0)
            if cmd == ["which", "rsync"]:
                return MagicMock(returncode=0)
            if cmd == ["which", "btrfs"]:
                return MagicMock(returncode=0)
            if cmd == [
                "stat",
                "-f",
                "-c",
                "%T",
                "/backup",
            ]:
                return MagicMock(returncode=0, stdout="btrfs\n")
            if cmd == [
                "stat",
                "-c",
                "%i",
                "/backup/backup",
            ]:
                return MagicMock(returncode=0, stdout="256\n")
            if cmd == [
                "findmnt",
                "-n",
                "-o",
                "OPTIONS",
                "/backup",
            ]:
                return MagicMock(returncode=0, stdout="rw,relatime\n")
            return MagicMock(returncode=0)

        mock_run.side_effect = remote_side_effect

        status = check_sync(sync, config, vol_statuses, _make_resolved(config))
        assert status.active is False
        assert SyncReason.DESTINATION_NOT_MOUNTED_USER_SUBVOL_RM in status.reasons

    @patch("nbkp.preflight.checks._check_rsync_version", return_value=True)
    @patch("nbkp.preflight.checks.run_remote_command")
    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/rsync",
    )
    def test_stat_not_found_on_remote_destination(
        self,
        mock_which: MagicMock,
        mock_run: MagicMock,
        _mock_rsync_ver: MagicMock,
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
        vol_statuses = {
            "src": VolumeStatus(
                slug="src",
                config=src_vol,
                reasons=[],
            ),
            "dst": VolumeStatus(
                slug="dst",
                config=dst_vol,
                reasons=[],
            ),
        }

        def remote_side_effect(
            server: SshEndpoint,
            cmd: list[str],
            proxy_chain: list[SshEndpoint] | None = None,
        ) -> MagicMock:
            if cmd == [
                "test",
                "-f",
                "/backup/backup/.nbkp-dst",
            ]:
                return MagicMock(returncode=0)
            if cmd == ["which", "rsync"]:
                return MagicMock(returncode=0)
            if cmd == ["which", "btrfs"]:
                return MagicMock(returncode=0)
            if cmd == ["which", "stat"]:
                return MagicMock(returncode=1)
            return MagicMock(returncode=0)

        mock_run.side_effect = remote_side_effect

        status = check_sync(sync, config, vol_statuses, _make_resolved(config))
        assert status.active is False
        assert SyncReason.STAT_NOT_FOUND_ON_DESTINATION in status.reasons
        assert SyncReason.DESTINATION_NOT_BTRFS not in status.reasons

    @patch("nbkp.preflight.checks._check_rsync_version", return_value=True)
    @patch("nbkp.preflight.checks.run_remote_command")
    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/rsync",
    )
    def test_findmnt_not_found_on_remote_destination(
        self,
        mock_which: MagicMock,
        mock_run: MagicMock,
        _mock_rsync_ver: MagicMock,
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
        vol_statuses = {
            "src": VolumeStatus(
                slug="src",
                config=src_vol,
                reasons=[],
            ),
            "dst": VolumeStatus(
                slug="dst",
                config=dst_vol,
                reasons=[],
            ),
        }

        def remote_side_effect(
            server: SshEndpoint,
            cmd: list[str],
            proxy_chain: list[SshEndpoint] | None = None,
        ) -> MagicMock:
            if cmd == [
                "test",
                "-f",
                "/backup/backup/.nbkp-dst",
            ]:
                return MagicMock(returncode=0)
            if cmd == ["which", "rsync"]:
                return MagicMock(returncode=0)
            if cmd == ["which", "btrfs"]:
                return MagicMock(returncode=0)
            if cmd == ["which", "findmnt"]:
                return MagicMock(returncode=1)
            if cmd == [
                "stat",
                "-f",
                "-c",
                "%T",
                "/backup",
            ]:
                return MagicMock(returncode=0, stdout="btrfs\n")
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
            return MagicMock(returncode=0)

        mock_run.side_effect = remote_side_effect

        status = check_sync(sync, config, vol_statuses, _make_resolved(config))
        assert status.active is False
        assert SyncReason.FINDMNT_NOT_FOUND_ON_DESTINATION in status.reasons
        assert SyncReason.DESTINATION_NOT_MOUNTED_USER_SUBVOL_RM not in status.reasons

    @patch("nbkp.preflight.checks._check_rsync_version", return_value=True)
    @patch("nbkp.preflight.checks.run_remote_command")
    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/rsync",
    )
    def test_destination_latest_not_found_on_remote(
        self,
        mock_which: MagicMock,
        mock_run: MagicMock,
        _mock_rsync_ver: MagicMock,
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
        vol_statuses = {
            "src": VolumeStatus(
                slug="src",
                config=src_vol,
                reasons=[],
            ),
            "dst": VolumeStatus(
                slug="dst",
                config=dst_vol,
                reasons=[],
            ),
        }

        def remote_side_effect(
            server: SshEndpoint,
            cmd: list[str],
            proxy_chain: list[SshEndpoint] | None = None,
        ) -> MagicMock:
            if cmd == [
                "test",
                "-f",
                "/backup/backup/.nbkp-dst",
            ]:
                return MagicMock(returncode=0)
            if cmd == ["which", "rsync"]:
                return MagicMock(returncode=0)
            if cmd == ["which", "btrfs"]:
                return MagicMock(returncode=0)
            if cmd == [
                "stat",
                "-f",
                "-c",
                "%T",
                "/backup",
            ]:
                return MagicMock(returncode=0, stdout="btrfs\n")
            if cmd == [
                "stat",
                "-c",
                "%i",
                "/backup/backup",
            ]:
                return MagicMock(returncode=0, stdout="256\n")
            if cmd == [
                "findmnt",
                "-n",
                "-o",
                "OPTIONS",
                "/backup",
            ]:
                return MagicMock(
                    returncode=0,
                    stdout="rw,user_subvol_rm_allowed\n",
                )
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
            return MagicMock(returncode=0)

        mock_run.side_effect = remote_side_effect

        status = check_sync(sync, config, vol_statuses, _make_resolved(config))
        assert status.active is False
        assert SyncReason.DESTINATION_TMP_NOT_FOUND in status.reasons
        assert SyncReason.DESTINATION_SNAPSHOTS_DIR_NOT_FOUND not in status.reasons

    @patch("nbkp.preflight.checks._check_rsync_version", return_value=True)
    @patch("nbkp.preflight.checks.run_remote_command")
    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/rsync",
    )
    def test_destination_snapshots_dir_not_found_on_remote(
        self,
        mock_which: MagicMock,
        mock_run: MagicMock,
        _mock_rsync_ver: MagicMock,
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
        vol_statuses = {
            "src": VolumeStatus(
                slug="src",
                config=src_vol,
                reasons=[],
            ),
            "dst": VolumeStatus(
                slug="dst",
                config=dst_vol,
                reasons=[],
            ),
        }

        def remote_side_effect(
            server: SshEndpoint,
            cmd: list[str],
            proxy_chain: list[SshEndpoint] | None = None,
        ) -> MagicMock:
            if cmd == [
                "test",
                "-f",
                "/backup/backup/.nbkp-dst",
            ]:
                return MagicMock(returncode=0)
            if cmd == ["which", "rsync"]:
                return MagicMock(returncode=0)
            if cmd == ["which", "btrfs"]:
                return MagicMock(returncode=0)
            if cmd == [
                "stat",
                "-f",
                "-c",
                "%T",
                "/backup",
            ]:
                return MagicMock(returncode=0, stdout="btrfs\n")
            if cmd == [
                "stat",
                "-c",
                "%i",
                "/backup/backup",
            ]:
                return MagicMock(returncode=0, stdout="256\n")
            if cmd == [
                "findmnt",
                "-n",
                "-o",
                "OPTIONS",
                "/backup",
            ]:
                return MagicMock(
                    returncode=0,
                    stdout="rw,user_subvol_rm_allowed\n",
                )
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
            return MagicMock(returncode=0)

        mock_run.side_effect = remote_side_effect

        status = check_sync(sync, config, vol_statuses, _make_resolved(config))
        assert status.active is False
        assert SyncReason.DESTINATION_SNAPSHOTS_DIR_NOT_FOUND in status.reasons
        assert SyncReason.DESTINATION_TMP_NOT_FOUND not in status.reasons


class TestCheckAllSyncs:
    @patch("nbkp.preflight.checks._check_rsync_version", return_value=True)
    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/rsync",
    )
    def test_check_all(
        self,
        mock_which: MagicMock,
        _mock_rsync_ver: MagicMock,
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

        vol_statuses, sync_statuses = check_all_syncs(config)
        assert vol_statuses["src"].active is True
        assert vol_statuses["dst"].active is True
        assert sync_statuses["s1"].active is True

    @patch("nbkp.preflight.checks._check_rsync_version", return_value=True)
    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/rsync",
    )
    def test_only_syncs_filters_syncs_and_volumes(
        self,
        mock_which: MagicMock,
        _mock_rsync_ver: MagicMock,
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

        vol_statuses, sync_statuses = check_all_syncs(config, only_syncs=["s1"])
        assert set(sync_statuses.keys()) == {"s1"}
        assert set(vol_statuses.keys()) == {"src1", "dst1"}
        assert sync_statuses["s1"].active is True


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

    def _make_active_vol_statuses(self, config: Config) -> dict[str, VolumeStatus]:
        return {
            "src": VolumeStatus(
                slug="src",
                config=config.volumes["src"],
                reasons=[],
            ),
            "dst": VolumeStatus(
                slug="dst",
                config=config.volumes["dst"],
                reasons=[],
            ),
        }

    @patch("nbkp.preflight.checks.subprocess.run")
    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/fake",
    )
    def test_snapshots_dir_not_found(
        self,
        mock_which: MagicMock,
        mock_subprocess: MagicMock,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_active_sentinels(src, dst)
        # No snapshots dir

        config, sync = self._make_hl_config(src, dst)
        vol_statuses = self._make_active_vol_statuses(config)

        mock_subprocess.return_value = MagicMock(returncode=0, stdout="ext2/ext3\n")

        status = check_sync(sync, config, vol_statuses)
        assert status.active is False
        assert SyncReason.DESTINATION_SNAPSHOTS_DIR_NOT_FOUND in status.reasons

    @patch("nbkp.preflight.checks.subprocess.run")
    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/fake",
    )
    def test_no_hardlink_support_fat(
        self,
        mock_which: MagicMock,
        mock_subprocess: MagicMock,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_active_sentinels(src, dst)
        (dst / "backup" / "snapshots").mkdir()

        config, sync = self._make_hl_config(src, dst)
        vol_statuses = self._make_active_vol_statuses(config)

        mock_subprocess.return_value = MagicMock(returncode=0, stdout="vfat\n")

        status = check_sync(sync, config, vol_statuses)
        assert status.active is False
        assert SyncReason.DESTINATION_NO_HARDLINK_SUPPORT in status.reasons

    @patch("nbkp.preflight.checks._check_rsync_version", return_value=True)
    @patch("nbkp.preflight.checks.subprocess.run")
    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/fake",
    )
    def test_active_with_ext4(
        self,
        mock_which: MagicMock,
        mock_subprocess: MagicMock,
        _mock_rsync_ver: MagicMock,
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
        vol_statuses = self._make_active_vol_statuses(config)

        mock_subprocess.return_value = MagicMock(returncode=0, stdout="ext2/ext3\n")

        status = check_sync(sync, config, vol_statuses)
        assert status.active is True
        assert status.reasons == []

    @patch(
        "nbkp.preflight.checks.shutil.which",
        side_effect=lambda cmd: None if cmd == "stat" else f"/usr/bin/{cmd}",
    )
    def test_stat_not_found(
        self,
        mock_which: MagicMock,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_active_sentinels(src, dst)

        config, sync = self._make_hl_config(src, dst)
        vol_statuses = self._make_active_vol_statuses(config)

        status = check_sync(sync, config, vol_statuses)
        assert status.active is False
        assert SyncReason.STAT_NOT_FOUND_ON_DESTINATION in status.reasons
        # No hardlink support check when stat is missing
        assert SyncReason.DESTINATION_NO_HARDLINK_SUPPORT not in status.reasons

    @patch("nbkp.preflight.checks.subprocess.run")
    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/fake",
    )
    def test_no_btrfs_checks_for_hardlink(
        self,
        mock_which: MagicMock,
        mock_subprocess: MagicMock,
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
        vol_statuses = self._make_active_vol_statuses(config)

        mock_subprocess.return_value = MagicMock(returncode=0, stdout="ext2/ext3\n")

        status = check_sync(sync, config, vol_statuses)
        assert SyncReason.BTRFS_NOT_FOUND_ON_DESTINATION not in status.reasons
        assert SyncReason.DESTINATION_NOT_BTRFS not in status.reasons
        assert SyncReason.DESTINATION_NOT_BTRFS_SUBVOLUME not in status.reasons
        assert SyncReason.DESTINATION_TMP_NOT_FOUND not in status.reasons

    @patch("nbkp.preflight.checks._check_rsync_version", return_value=True)
    @patch("nbkp.preflight.checks.run_remote_command")
    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/rsync",
    )
    def test_remote_no_hardlink_support(
        self,
        mock_which: MagicMock,
        mock_run: MagicMock,
        _mock_rsync_ver: MagicMock,
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
        vol_statuses = {
            "src": VolumeStatus(
                slug="src",
                config=src_vol,
                reasons=[],
            ),
            "dst": VolumeStatus(
                slug="dst",
                config=dst_vol,
                reasons=[],
            ),
        }

        def remote_side_effect(
            server: SshEndpoint,
            cmd: list[str],
            proxy_chain: list[SshEndpoint] | None = None,
        ) -> MagicMock:
            if cmd == [
                "test",
                "-f",
                "/backup/backup/.nbkp-dst",
            ]:
                return MagicMock(returncode=0)
            if cmd == ["which", "rsync"]:
                return MagicMock(returncode=0)
            if cmd == ["which", "stat"]:
                return MagicMock(returncode=0)
            if cmd == [
                "stat",
                "-f",
                "-c",
                "%T",
                "/backup",
            ]:
                return MagicMock(returncode=0, stdout="exfat\n")
            if cmd == [
                "test",
                "-d",
                "/backup/backup/snapshots",
            ]:
                return MagicMock(returncode=0)
            return MagicMock(returncode=0)

        mock_run.side_effect = remote_side_effect

        status = check_sync(sync, config, vol_statuses, _make_resolved(config))
        assert status.active is False
        assert SyncReason.DESTINATION_NO_HARDLINK_SUPPORT in status.reasons


class TestCheckSourceLatest:
    """Tests for SOURCE_LATEST_NOT_FOUND check."""

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

    def _active_vol_statuses(self, config: Config) -> dict[str, VolumeStatus]:
        return {
            "src": VolumeStatus(
                slug="src",
                config=config.volumes["src"],
                reasons=[],
            ),
            "dst": VolumeStatus(
                slug="dst",
                config=config.volumes["dst"],
                reasons=[],
            ),
        }

    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/rsync",
    )
    def test_btrfs_source_latest_missing(
        self, mock_which: MagicMock, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_sentinels(src, dst)
        # No latest/ under source

        config, sync = self._make_config(src, dst, "btrfs")
        vol_statuses = self._active_vol_statuses(config)

        status = check_sync(sync, config, vol_statuses)
        assert SyncReason.SOURCE_LATEST_NOT_FOUND in status.reasons

    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/rsync",
    )
    def test_hard_link_source_latest_missing(
        self, mock_which: MagicMock, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_sentinels(src, dst)
        # No latest/ under source

        config, sync = self._make_config(src, dst, "hard-link")
        vol_statuses = self._active_vol_statuses(config)

        status = check_sync(sync, config, vol_statuses)
        assert SyncReason.SOURCE_LATEST_NOT_FOUND in status.reasons

    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/rsync",
    )
    def test_btrfs_source_latest_present(
        self, mock_which: MagicMock, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_sentinels(src, dst)
        (src / "data" / "snapshots").mkdir(exist_ok=True)
        snap = src / "data" / "snapshots" / "2026-01-01T00:00:00.000Z"
        snap.mkdir()
        (src / "data" / "latest").symlink_to("snapshots/2026-01-01T00:00:00.000Z")

        config, sync = self._make_config(src, dst, "btrfs")
        vol_statuses = self._active_vol_statuses(config)

        status = check_sync(sync, config, vol_statuses)
        assert SyncReason.SOURCE_LATEST_NOT_FOUND not in status.reasons
        assert SyncReason.SOURCE_LATEST_INVALID not in status.reasons

    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/rsync",
    )
    def test_hard_link_source_latest_symlink(
        self, mock_which: MagicMock, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_sentinels(src, dst)
        snap = src / "data" / "snapshots" / "2024-01-01T00:00:00.000Z"
        snap.mkdir(parents=True)
        (src / "data" / "latest").symlink_to("snapshots/2024-01-01T00:00:00.000Z")

        config, sync = self._make_config(src, dst, "hard-link")
        vol_statuses = self._active_vol_statuses(config)

        status = check_sync(sync, config, vol_statuses)
        assert SyncReason.SOURCE_LATEST_NOT_FOUND not in status.reasons

    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/rsync",
    )
    def test_no_snapshots_source_skips_check(
        self, mock_which: MagicMock, tmp_path: Path
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
        vol_statuses = self._active_vol_statuses(config)

        status = check_sync(sync, config, vol_statuses)
        assert SyncReason.SOURCE_LATEST_NOT_FOUND not in status.reasons


class TestCheckSourceSnapshots:
    """Tests for SOURCE_SNAPSHOTS_DIR_NOT_FOUND check."""

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

    def _active_vol_statuses(self, config: Config) -> dict[str, VolumeStatus]:
        return {
            "src": VolumeStatus(
                slug="src",
                config=config.volumes["src"],
                reasons=[],
            ),
            "dst": VolumeStatus(
                slug="dst",
                config=config.volumes["dst"],
                reasons=[],
            ),
        }

    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/rsync",
    )
    def test_btrfs_source_snapshots_missing(
        self, mock_which: MagicMock, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_sentinels(src, dst)
        (src / "data" / "latest").symlink_to("/dev/null")
        # No snapshots/ dir

        config, sync = self._make_config(src, dst, "btrfs")
        vol_statuses = self._active_vol_statuses(config)

        status = check_sync(sync, config, vol_statuses)
        assert SyncReason.SOURCE_SNAPSHOTS_DIR_NOT_FOUND in status.reasons

    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/rsync",
    )
    def test_hard_link_source_snapshots_missing(
        self, mock_which: MagicMock, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_sentinels(src, dst)
        (src / "data" / "latest").symlink_to("/dev/null")
        # No snapshots/ dir

        config, sync = self._make_config(src, dst, "hard-link")
        vol_statuses = self._active_vol_statuses(config)

        status = check_sync(sync, config, vol_statuses)
        assert SyncReason.SOURCE_SNAPSHOTS_DIR_NOT_FOUND in status.reasons

    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/rsync",
    )
    def test_btrfs_source_snapshots_present(
        self, mock_which: MagicMock, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_sentinels(src, dst)
        (src / "data" / "snapshots").mkdir()
        (src / "data" / "latest").symlink_to("/dev/null")

        config, sync = self._make_config(src, dst, "btrfs")
        vol_statuses = self._active_vol_statuses(config)

        status = check_sync(sync, config, vol_statuses)
        assert SyncReason.SOURCE_SNAPSHOTS_DIR_NOT_FOUND not in status.reasons

    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/rsync",
    )
    def test_no_snapshots_source_skips_check(
        self, mock_which: MagicMock, tmp_path: Path
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
        vol_statuses = self._active_vol_statuses(config)

        status = check_sync(sync, config, vol_statuses)
        assert SyncReason.SOURCE_SNAPSHOTS_DIR_NOT_FOUND not in status.reasons


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

    def _active_vol_statuses(self, config: Config) -> dict[str, VolumeStatus]:
        return {
            slug: VolumeStatus(slug=slug, config=vol, reasons=[])
            for slug, vol in config.volumes.items()
        }

    # ── Source latest → /dev/null with upstream sync ────

    @patch("nbkp.preflight.checks._check_rsync_version", return_value=True)
    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/fake",
    )
    def test_source_devnull_accepted_with_upstream(
        self,
        mock_which: MagicMock,
        _mock_rsync_ver: MagicMock,
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
        vol_statuses = self._active_vol_statuses(config)

        status = check_sync(sync, config, vol_statuses, all_syncs=config.syncs)
        assert SyncReason.SOURCE_LATEST_INVALID not in status.reasons
        assert SyncReason.SOURCE_LATEST_NOT_FOUND not in status.reasons

    # ── Source latest → /dev/null with upstream sync (dry-run) ────

    @patch("nbkp.preflight.checks._check_rsync_version", return_value=True)
    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/fake",
    )
    def test_source_devnull_inactive_in_dry_run_with_upstream(
        self,
        mock_which: MagicMock,
        _mock_rsync_ver: MagicMock,
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
        vol_statuses = self._active_vol_statuses(config)

        status = check_sync(
            sync, config, vol_statuses, all_syncs=config.syncs, dry_run=True
        )
        assert SyncReason.DRY_RUN_SOURCE_SNAPSHOT_PENDING in status.reasons
        assert not status.active

    # ── Source latest → /dev/null without upstream sync ────

    @patch("nbkp.preflight.checks._check_rsync_version", return_value=True)
    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/fake",
    )
    def test_source_devnull_rejected_without_upstream(
        self,
        mock_which: MagicMock,
        _mock_rsync_ver: MagicMock,
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
        vol_statuses = self._active_vol_statuses(config)

        status = check_sync(sync, config, vol_statuses)
        assert SyncReason.SOURCE_LATEST_INVALID in status.reasons

    # ── Source latest → dangling path ────

    @patch("nbkp.preflight.checks._check_rsync_version", return_value=True)
    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/fake",
    )
    def test_source_dangling_symlink_invalid(
        self,
        mock_which: MagicMock,
        _mock_rsync_ver: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Source latest pointing to non-existent snapshot is
        invalid."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_sentinels(src, dst)
        (src / "data" / "snapshots").mkdir()
        (src / "data" / "latest").symlink_to("snapshots/2099-01-01T00:00:00.000Z")
        (dst / "backup" / "snapshots").mkdir()
        (dst / "backup" / "latest").symlink_to("/dev/null")

        config, sync = self._make_config(src, dst)
        vol_statuses = self._active_vol_statuses(config)

        status = check_sync(sync, config, vol_statuses)
        assert SyncReason.SOURCE_LATEST_INVALID in status.reasons

    # ── Destination latest → /dev/null (valid) ────

    @patch("nbkp.preflight.checks._check_rsync_version", return_value=True)
    @patch("nbkp.preflight.checks.subprocess.run")
    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/fake",
    )
    def test_dest_devnull_accepted(
        self,
        mock_which: MagicMock,
        mock_subprocess: MagicMock,
        _mock_rsync_ver: MagicMock,
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

        mock_subprocess.return_value = MagicMock(returncode=0, stdout="ext2/ext3\n")

        config, sync = self._make_config(src, dst)
        vol_statuses = self._active_vol_statuses(config)

        status = check_sync(sync, config, vol_statuses)
        assert SyncReason.DESTINATION_LATEST_NOT_FOUND not in status.reasons
        assert SyncReason.DESTINATION_LATEST_INVALID not in status.reasons

    # ── Destination latest missing ────

    @patch("nbkp.preflight.checks._check_rsync_version", return_value=True)
    @patch("nbkp.preflight.checks.subprocess.run")
    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/fake",
    )
    def test_dest_latest_missing(
        self,
        mock_which: MagicMock,
        mock_subprocess: MagicMock,
        _mock_rsync_ver: MagicMock,
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

        mock_subprocess.return_value = MagicMock(returncode=0, stdout="ext2/ext3\n")

        config, sync = self._make_config(src, dst)
        vol_statuses = self._active_vol_statuses(config)

        status = check_sync(sync, config, vol_statuses)
        assert SyncReason.DESTINATION_LATEST_NOT_FOUND in status.reasons

    # ── Destination latest → dangling path ────

    @patch("nbkp.preflight.checks._check_rsync_version", return_value=True)
    @patch("nbkp.preflight.checks.subprocess.run")
    @patch(
        "nbkp.preflight.checks.shutil.which",
        return_value="/usr/bin/fake",
    )
    def test_dest_dangling_symlink_invalid(
        self,
        mock_which: MagicMock,
        mock_subprocess: MagicMock,
        _mock_rsync_ver: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Destination latest pointing to non-existent snapshot is
        invalid."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        self._setup_sentinels(src, dst)
        (dst / "backup" / "snapshots").mkdir()
        (dst / "backup" / "latest").symlink_to("snapshots/2099-01-01T00:00:00.000Z")

        mock_subprocess.return_value = MagicMock(returncode=0, stdout="ext2/ext3\n")

        config, sync = self._make_config(src, dst)
        vol_statuses = self._active_vol_statuses(config)

        status = check_sync(sync, config, vol_statuses)
        assert SyncReason.DESTINATION_LATEST_INVALID in status.reasons


class TestCheckRemoteVolumeSpaces:
    @patch("nbkp.preflight.checks.run_remote_command")
    def test_active(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        vol, config = _remote_config(path="/my backup")
        resolved = _make_resolved(config)
        status = check_volume(vol, resolved)
        assert status.active is True
        server = config.ssh_endpoints["nas-server"]
        mock_run.assert_called_once_with(
            server,
            ["test", "-f", "/my backup/.nbkp-vol"],
            [],
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


class TestCheckRsyncVersionLocal:
    @patch("nbkp.preflight.checks.subprocess.run")
    def test_new_enough(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="rsync  version 3.2.7  protocol version 31\n",
        )
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert _check_rsync_version(vol, {}) is True

    @patch("nbkp.preflight.checks.subprocess.run")
    def test_too_old(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="rsync  version 2.6.9  protocol version 29\n",
        )
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert _check_rsync_version(vol, {}) is False

    @patch("nbkp.preflight.checks.subprocess.run")
    def test_openrsync(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=("openrsync: protocol version 29\nrsync version 2.6.9 compatible\n"),
        )
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert _check_rsync_version(vol, {}) is False

    @patch("nbkp.preflight.checks.subprocess.run")
    def test_command_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert _check_rsync_version(vol, {}) is False

    @patch("nbkp.preflight.checks.subprocess.run")
    def test_exactly_3_0_0(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="rsync  version 3.0.0  protocol version 30\n",
        )
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert _check_rsync_version(vol, {}) is True


class TestCheckRsyncVersionRemote:
    @patch("nbkp.preflight.checks.run_remote_command")
    def test_new_enough(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="rsync  version 3.2.7  protocol version 31\n",
        )
        vol, config = _remote_config()
        resolved = _make_resolved(config)
        assert _check_rsync_version(vol, resolved) is True

    @patch("nbkp.preflight.checks.run_remote_command")
    def test_too_old(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="rsync  version 2.6.9  protocol version 29\n",
        )
        vol, config = _remote_config()
        resolved = _make_resolved(config)
        assert _check_rsync_version(vol, resolved) is False
