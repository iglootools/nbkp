"""Volume models: local and remote filesystem volumes."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, List, Literal, Optional, Union

from pydantic import ConfigDict, Field, field_validator

from .base import Slug, _BaseModel

MountStrategyType = Literal["auto", "systemd", "direct"]


class LuksEncryptionConfig(_BaseModel):
    """LUKS encryption config.

    ``type='luks'`` serves as discriminator for future encryption backends
    (e.g. VeraCrypt, BitLocker).
    """

    model_config = ConfigDict(frozen=True)
    type: Literal["luks"] = "luks"
    mapper_name: str = Field(
        ...,
        min_length=1,
        pattern=r"^[a-zA-Z0-9]([a-zA-Z0-9_-]*[a-zA-Z0-9])?$",
        description="Device mapper name (e.g. ``seagate8tb``)",
    )
    passphrase_id: str = Field(
        ...,
        min_length=1,
        description="Credential lookup key for the passphrase",
    )


# Future: VeraCryptEncryptionConfig, etc.
EncryptionConfig = Annotated[Union[LuksEncryptionConfig], Field(discriminator="type")]

_UUID_PATTERN = (
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class MountConfig(_BaseModel):
    """Mount management config for a volume.

    When present, nbkp manages the volume's mount lifecycle:
    detect drive → (attach LUKS if encrypted) → mount → backup → umount → (close LUKS).
    """

    model_config = ConfigDict(frozen=True)
    strategy: MountStrategyType = Field(
        default="auto",
        description=(
            "Mount strategy: ``systemd`` uses systemctl and systemd-cryptsetup,"
            " ``direct`` uses raw mount/umount/cryptsetup commands,"
            " ``auto`` probes for systemctl and picks accordingly."
        ),
    )
    device_uuid: str = Field(
        ...,
        min_length=1,
        pattern=_UUID_PATTERN,
        description=(
            "Device UUID for drive detection (from ``/dev/disk/by-uuid/``)."
            " For encrypted volumes: the LUKS container UUID (from crypttab)."
            " For unencrypted volumes: the filesystem UUID (from fstab/blkid)."
        ),
    )
    encryption: Optional[EncryptionConfig] = Field(
        default=None,
        description="Encryption config. Omit for unencrypted volumes.",
    )


class LocalVolume(_BaseModel):
    """A local filesystem volume."""

    model_config = ConfigDict(frozen=True)
    type: Literal["local"] = "local"
    slug: Slug
    path: str = Field(
        ...,
        min_length=1,
        description=(
            "Absolute path to the volume."
            " `~` is expanded to the user's home directory."
            " Trailing slashes are stripped."
        ),
    )
    mount: Optional[MountConfig] = Field(
        default=None,
        description=(
            "Mount management config."
            " When set, nbkp manages the volume's mount lifecycle."
        ),
    )

    @field_validator("path", mode="before")
    @classmethod
    def normalize_path(cls, v: Any) -> str:
        if not isinstance(v, str):
            return v  # type: ignore[no-any-return, return-value]
        return str(Path(v).expanduser())


class RemoteVolume(_BaseModel):
    """A remote volume accessible via SSH."""

    model_config = ConfigDict(frozen=True)
    type: Literal["remote"] = "remote"
    slug: Slug
    ssh_endpoint: str = Field(
        ..., min_length=1, description="Primary SSH endpoint slug"
    )
    ssh_endpoints: Optional[List[str]] = Field(
        default=None, description="Candidate endpoints for auto-selection"
    )
    path: str = Field(
        ...,
        min_length=1,
        description=(
            "Absolute path on the remote host."
            " Trailing slashes are stripped."
            " `~` is not expanded"
            " (it refers to the remote user's home"
            " and is resolved by SSH/rsync)."
        ),
    )
    mount: Optional[MountConfig] = Field(
        default=None,
        description=(
            "Mount management config."
            " When set, nbkp manages the volume's mount lifecycle."
        ),
    )

    @field_validator("path", mode="before")
    @classmethod
    def normalize_path(cls, v: Any) -> str:
        if not isinstance(v, str):
            return v  # type: ignore[no-any-return, return-value]
        stripped = v.rstrip("/")
        return stripped if stripped else "/"


Volume = Annotated[Union[LocalVolume, RemoteVolume], Field(discriminator="type")]
