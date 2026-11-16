"""Tests for the config models: construction, defaults and validation."""

from pathlib import Path

import pydantic
import pytest

from nbkp.config import (
    Config,
    ConfigValidationCode,
    LocalVolume,
    RemoteVolume,
    RsyncOptions,
    SshConnectionOptions,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)


def _error_types(error: pydantic.ValidationError) -> set[str]:
    return {e["type"] for e in error.errors()}


class TestLocalVolume:
    def test_construction(self) -> None:
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert vol.slug == "data"
        assert vol.path == "/mnt/data"

    def test_frozen(self) -> None:
        vol = LocalVolume(slug="data", path="/mnt/data")
        try:
            vol.slug = "other"  # type: ignore[misc]
            assert False, "Should be frozen"
        except AttributeError, pydantic.ValidationError:
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
        vol = RemoteVolume(
            slug="nas",
            ssh_endpoint="nas-server",
            path="/backup",
        )
        try:
            vol.path = "other"  # type: ignore[misc]
            assert False, "Should be frozen"
        except AttributeError, pydantic.ValidationError:
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
        assert sc.rsync_options.checksum is False
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
        assert sc.rsync_options.checksum is False
        assert sc.filters == ["+ *.jpg", "- *.tmp"]
        assert sc.filter_file == "/etc/nbkp/filters.rules"


class TestConfig:
    def test_empty(self) -> None:
        cfg = Config()
        assert cfg.volumes == {}
        assert cfg.syncs == {}

    def test_with_data(self) -> None:
        cfg = Config(
            volumes={
                "src-vol": LocalVolume(slug="src-vol", path="/mnt/src"),
                "dst-vol": LocalVolume(slug="dst-vol", path="/mnt/dst"),
            },
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src-vol"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst-vol"),
            },
            syncs={
                "s1": SyncConfig(
                    slug="s1",
                    source="ep-src",
                    destination="ep-dst",
                ),
            },
        )
        assert "src-vol" in cfg.volumes
        assert "s1" in cfg.syncs


class TestCrossServerValidation:
    def test_cross_server_remote_to_remote_rejected(self) -> None:
        with pytest.raises(
            pydantic.ValidationError,
            check=lambda e: _error_types(e) == {ConfigValidationCode.CROSS_SERVER_SYNC},
        ):
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

    @pytest.mark.parametrize(
        ("slug", "error_type"),
        [
            ("MyDrive", "string_pattern_mismatch"),
            ("my_drive", "string_pattern_mismatch"),
            ("my drive", "string_pattern_mismatch"),
            ("drive-", "string_pattern_mismatch"),
            ("-drive", "string_pattern_mismatch"),
            ("", "string_too_short"),
            ("a" * 51, "string_too_long"),
        ],
    )
    def test_invalid(self, slug: str, error_type: str) -> None:
        with pytest.raises(pydantic.ValidationError) as excinfo:
            LocalVolume(slug=slug, path="/mnt")
        assert [(e["loc"], e["type"]) for e in excinfo.value.errors()] == [
            (("slug",), error_type)
        ]

    def test_valid_max_length(self) -> None:
        vol = LocalVolume(slug="a" * 50, path="/mnt")
        assert len(vol.slug) == 50
