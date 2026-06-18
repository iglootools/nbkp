"""Volume models: local and remote filesystem volumes."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, List, Literal, Optional, Union

from pydantic import ConfigDict, Field, field_validator, model_validator

from .base import Slug, _BaseModel


class LuksEncryptionConfig(_BaseModel):
    """LUKS encryption config.

    ``type='luks'`` serves as discriminator for future encryption backends
    (e.g. VeraCrypt, BitLocker).
    """

    model_config = ConfigDict(frozen=True)
    type: Literal["luks"] = "luks"
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
    detect drive → (unlock LUKS if encrypted) → mount → backup → umount → (lock LUKS).
    """

    model_config = ConfigDict(frozen=True)
    device_uuid: str = Field(
        ...,
        min_length=1,
        pattern=_UUID_PATTERN,
        description=(
            "Device UUID for drive detection (from ``/dev/disk/by-uuid/``)."
            " For encrypted volumes: the LUKS container UUID (from crypttab/blkid);"
            " the unlocked device is named ``luks-<uuid>`` by udisks unless a"
            " crypttab entry overrides it."
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
    path: Optional[str] = Field(
        default=None,
        min_length=1,
        description=(
            "Absolute path to the volume."
            " `~` is expanded to the user's home directory."
            " Trailing slashes are stripped."
            " Optional only for mount-managed volumes: when omitted, the"
            " mountpoint udisks chooses (``/run/media/<user>/<label>``) is"
            " discovered at runtime. Required when ``mount`` is not set."
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
    def normalize_path(cls, v: Any) -> Any:
        if not isinstance(v, str):
            return v
        return str(Path(v).expanduser())

    @model_validator(mode="after")
    def _require_path_without_mount(self) -> "LocalVolume":
        _validate_path_requirement(self)
        return self


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
    path: Optional[str] = Field(
        default=None,
        min_length=1,
        description=(
            "Absolute path on the remote host."
            " Trailing slashes are stripped."
            " `~` is not expanded"
            " (it refers to the remote user's home"
            " and is resolved by SSH/rsync)."
            " Optional only for mount-managed volumes: when omitted, the"
            " mountpoint udisks chooses (``/run/media/<user>/<label>``) is"
            " discovered at runtime. Required when ``mount`` is not set."
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
    def normalize_path(cls, v: Any) -> Any:
        if not isinstance(v, str):
            return v
        stripped = v.rstrip("/")
        return stripped if stripped else "/"

    @model_validator(mode="after")
    def _require_path_without_mount(self) -> "RemoteVolume":
        _validate_path_requirement(self)
        return self


def _validate_path_requirement(volume: "LocalVolume | RemoteVolume") -> None:
    """Enforce that ``path`` is set unless the volume is mount-managed.

    Mount-managed volumes may omit ``path`` (the mountpoint is discovered at
    runtime). Externally-mounted volumes must declare ``path`` — it is their
    only locator.
    """
    if volume.mount is None and volume.path is None:
        msg = (
            f"volume '{volume.slug}': 'path' is required for volumes without a"
            " 'mount' section"
        )
        raise ValueError(msg)


Volume = Annotated[Union[LocalVolume, RemoteVolume], Field(discriminator="type")]
