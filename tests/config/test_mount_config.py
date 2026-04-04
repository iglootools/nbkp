"""Tests for mount and encryption config models."""

from __future__ import annotations

import pytest

from nbkp.config import (
    Config,
    CredentialProvider,
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
    RemoteVolume,
    SyncConfig,
    SyncEndpoint,
)


class TestMountConfig:
    def test_unencrypted_mount(self) -> None:
        mount = MountConfig(device_uuid="5941f273-f73c-44c5-a3ef-fae7248db1b6")
        assert mount.device_uuid == "5941f273-f73c-44c5-a3ef-fae7248db1b6"
        assert mount.encryption is None

    def test_encrypted_mount(self) -> None:
        mount = MountConfig(
            device_uuid="5941f273-f73c-44c5-a3ef-fae7248db1b6",
            encryption=LuksEncryptionConfig(
                mapper_name="seagate8tb",
                passphrase_id="seagate8tb",
            ),
        )
        assert mount.encryption is not None
        assert mount.encryption.type == "luks"
        assert mount.encryption.mapper_name == "seagate8tb"
        assert mount.encryption.passphrase_id == "seagate8tb"

    def test_invalid_uuid_rejected(self) -> None:
        with pytest.raises(ValueError, match="String should match pattern"):
            MountConfig(device_uuid="not-a-uuid")

    def test_valid_uuid_formats(self) -> None:
        # lowercase
        m1 = MountConfig(device_uuid="5941f273-f73c-44c5-a3ef-fae7248db1b6")
        assert m1.device_uuid == "5941f273-f73c-44c5-a3ef-fae7248db1b6"
        # uppercase
        m2 = MountConfig(device_uuid="5941F273-F73C-44C5-A3EF-FAE7248DB1B6")
        assert m2.device_uuid == "5941F273-F73C-44C5-A3EF-FAE7248DB1B6"


class TestLuksEncryptionConfig:
    def test_type_defaults_to_luks(self) -> None:
        cfg = LuksEncryptionConfig(mapper_name="disk1", passphrase_id="disk1")
        assert cfg.type == "luks"

    def test_invalid_mapper_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="String should match pattern"):
            LuksEncryptionConfig(
                mapper_name="bad name!",
                passphrase_id="disk1",
            )

    def test_mapper_name_with_hyphens(self) -> None:
        cfg = LuksEncryptionConfig(
            mapper_name="my-disk-1",
            passphrase_id="my-disk",
        )
        assert cfg.mapper_name == "my-disk-1"

    def test_mapper_name_with_underscores(self) -> None:
        cfg = LuksEncryptionConfig(
            mapper_name="my_disk_1",
            passphrase_id="my-disk",
        )
        assert cfg.mapper_name == "my_disk_1"

    def test_empty_mapper_name_rejected(self) -> None:
        with pytest.raises(ValueError):
            LuksEncryptionConfig(mapper_name="", passphrase_id="disk1")

    def test_empty_passphrase_id_rejected(self) -> None:
        with pytest.raises(ValueError):
            LuksEncryptionConfig(mapper_name="disk1", passphrase_id="")


class TestVolumeWithMount:
    def test_local_volume_with_mount(self) -> None:
        vol = LocalVolume(
            slug="seagate8tb",
            path="/mnt/seagate8tb",
            mount=MountConfig(
                device_uuid="5941f273-f73c-44c5-a3ef-fae7248db1b6",
                encryption=LuksEncryptionConfig(
                    mapper_name="seagate8tb",
                    passphrase_id="seagate8tb",
                ),
            ),
        )
        assert vol.mount is not None
        assert vol.disks.encryption is not None

    def test_local_volume_without_mount(self) -> None:
        vol = LocalVolume(slug="data", path="/mnt/data")
        assert vol.mount is None

    def test_remote_volume_with_mount(self) -> None:
        vol = RemoteVolume(
            slug="nas-encrypted",
            ssh_endpoint="nas",
            path="/mnt/seagate8tb",
            mount=MountConfig(
                device_uuid="5941f273-f73c-44c5-a3ef-fae7248db1b6",
                encryption=LuksEncryptionConfig(
                    mapper_name="seagate8tb",
                    passphrase_id="nas-seagate8tb",
                ),
            ),
        )
        assert vol.mount is not None

    def test_remote_volume_without_mount(self) -> None:
        vol = RemoteVolume(
            slug="nas",
            ssh_endpoint="nas",
            path="/volume1/backups",
        )
        assert vol.mount is None


class TestCredentialProviderConfig:
    def _minimal_config(self, **kwargs: object) -> Config:
        return Config(
            **kwargs,  # type: ignore[arg-type]
            volumes={
                "src": LocalVolume(slug="src", path="/src"),
                "dst": LocalVolume(slug="dst", path="/dst"),
            },
            sync_endpoints={
                "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
                "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
            },
            syncs={
                "s1": SyncConfig(slug="s1", source="ep-src", destination="ep-dst"),
            },
        )

    def test_default_provider_is_keyring(self) -> None:
        cfg = self._minimal_config()
        assert cfg.credential_provider == CredentialProvider.KEYRING

    def test_provider_prompt(self) -> None:
        cfg = self._minimal_config(credential_provider="prompt")
        assert cfg.credential_provider == CredentialProvider.PROMPT

    def test_provider_env(self) -> None:
        cfg = self._minimal_config(credential_provider="env")
        assert cfg.credential_provider == CredentialProvider.ENV

    def test_provider_command_requires_credential_command(self) -> None:
        with pytest.raises(ValueError, match="credential-command is required"):
            self._minimal_config(credential_provider="command")

    def test_provider_command_with_credential_command(self) -> None:
        cfg = self._minimal_config(
            credential_provider="command",
            credential_command=["pass", "show", "nbkp/{id}"],
        )
        assert cfg.credential_provider == CredentialProvider.COMMAND
        assert cfg.credential_command == ["pass", "show", "nbkp/{id}"]

    def test_credential_command_must_contain_id_placeholder(self) -> None:
        with pytest.raises(ValueError, match="\\{id\\}"):
            self._minimal_config(
                credential_provider="command",
                credential_command=["pass", "show", "nbkp/fixed"],
            )

    def test_credential_command_without_command_provider(self) -> None:
        # credential-command can be set with other providers
        # (ignored but valid, no error)
        cfg = self._minimal_config(
            credential_provider="keyring",
            credential_command=["pass", "show", "nbkp/{id}"],
        )
        assert cfg.credential_command is not None
