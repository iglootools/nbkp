"""Tests for the config models: construction, defaults and validation."""

from pathlib import Path

import pydantic
import pytest

from nbkp.config import (
    Config,
    ConfigValidationErrorKind,
    CredentialProvider,
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
    RemoteVolume,
    RsyncOptions,
    SshConnectionOptions,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
    sync_predecessors,
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
        with pytest.raises((AttributeError, pydantic.ValidationError)):
            vol.slug = "other"


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
        with pytest.raises((AttributeError, pydantic.ValidationError)):
            vol.path = "other"


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
        assert sc.rsync_options.vanished_files == "fail"
        assert sc.filters == []
        assert sc.filter_file is None

    def test_vanished_files_reads_its_kebab_case_key(self) -> None:
        options = RsyncOptions.model_validate({"vanished-files": "warn"})
        assert options.vanished_files == "warn"

    def test_vanished_files_rejects_an_unknown_value(self) -> None:
        with pytest.raises(pydantic.ValidationError):
            RsyncOptions.model_validate({"vanished-files": "ignore"})

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
            check=lambda e: (
                _error_types(e) == {ConfigValidationErrorKind.CROSS_SERVER_SYNC}
            ),
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
                    ),
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
                ),
            },
        )
        assert "x" in config.syncs


def _remote_to_remote_config(
    src_candidates: list[str],
    dst_candidates: list[str],
) -> Config:
    return Config(
        ssh_endpoints={
            "lan": SshEndpoint(slug="lan", host="nas.lan"),
            "wan": SshEndpoint(slug="wan", host="nas.example.com"),
        },
        volumes={
            "src": RemoteVolume(
                slug="src",
                ssh_endpoint="lan",
                ssh_endpoints=src_candidates,
                path="/s",
            ),
            "dst": RemoteVolume(
                slug="dst",
                ssh_endpoint="lan",
                ssh_endpoints=dst_candidates,
                path="/d",
            ),
        },
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
        },
        syncs={"x": SyncConfig(slug="x", source="ep-src", destination="ep-dst")},
    )


class TestCrossServerCandidates:
    """Each volume resolves from its own candidates, so the lists must match."""

    @pytest.mark.parametrize(
        ("src_candidates", "dst_candidates"),
        [(["lan", "wan"], ["lan"]), (["lan", "wan"], ["wan", "lan"])],
    )
    def test_differing_candidates_rejected(
        self,
        src_candidates: list[str],
        dst_candidates: list[str],
    ) -> None:
        with pytest.raises(pydantic.ValidationError) as excinfo:
            _remote_to_remote_config(src_candidates, dst_candidates)
        assert _error_types(excinfo.value) == {
            ConfigValidationErrorKind.CROSS_SERVER_SYNC,
        }

    def test_same_candidates_allowed(self) -> None:
        config = _remote_to_remote_config(["lan", "wan"], ["lan", "wan"])
        assert "x" in config.syncs


def _encrypted_volumes_config(
    provider: CredentialProvider,
    *passphrase_ids: str,
) -> Config:
    return Config(
        credential_provider=provider,
        volumes={
            f"v{i}": LocalVolume(
                slug=f"v{i}",
                mount=MountConfig(
                    device_uuid=f"00000000-0000-0000-0000-00000000000{i}",
                    encryption=LuksEncryptionConfig(passphrase_id=pid),
                ),
            )
            for i, pid in enumerate(passphrase_ids)
        },
    )


class TestPassphraseEnvVarCollision:
    def test_ids_sharing_a_variable_rejected_with_env_provider(self) -> None:
        with pytest.raises(pydantic.ValidationError) as excinfo:
            _encrypted_volumes_config(CredentialProvider.ENV, "usb-drive", "usb_drive")
        assert _error_types(excinfo.value) == {
            ConfigValidationErrorKind.PASSPHRASE_ENV_VAR_COLLISION,
        }

    def test_shared_id_allowed(self) -> None:
        config = _encrypted_volumes_config(CredentialProvider.ENV, "usb", "usb")
        assert len(config.volumes) == 2

    def test_ids_sharing_a_variable_allowed_with_other_providers(self) -> None:
        config = _encrypted_volumes_config(
            CredentialProvider.KEYRING,
            "usb-drive",
            "usb_drive",
        )
        assert len(config.volumes) == 2


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
            (("slug",), error_type),
        ]

    def test_valid_max_length(self) -> None:
        vol = LocalVolume(slug="a" * 50, path="/mnt")
        assert len(vol.slug) == 50


def _sync_between(slug: str, source: str, destination: str) -> SyncConfig:
    return SyncConfig(slug=slug, source=source, destination=destination)


class TestSyncPredecessors:
    def test_no_dependencies(self) -> None:
        syncs = {
            "a": _sync_between("a", "ep-v1", "ep-v2"),
            "b": _sync_between("b", "ep-v3", "ep-v4"),
        }
        preds = sync_predecessors(syncs)
        assert preds == {"a": set(), "b": set()}

    def test_chain_dependency(self) -> None:
        syncs = {
            "a": _sync_between("a", "ep-v0", "ep-v1"),
            "b": _sync_between("b", "ep-v1", "ep-v2"),
            "c": _sync_between("c", "ep-v2", "ep-v3"),
        }
        preds = sync_predecessors(syncs)
        assert preds["a"] == set()
        assert preds["b"] == {"a"}
        assert preds["c"] == {"b"}
