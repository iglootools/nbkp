from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator, model_validator

from .base import ConfigModel, Slug
from .errors import ConfigValidationErrorKind, config_error


class LuksEncryptionConfig(ConfigModel):
    """``type='luks'`` serves as discriminator for future encryption backends
    (e.g. VeraCrypt, BitLocker).
    """

    type: Literal["luks"] = "luks"
    passphrase_id: str = Field(
        ...,
        min_length=1,
        description="Credential lookup key for the passphrase",
    )


# Future: VeraCryptEncryptionConfig, etc.
EncryptionConfig = Annotated[LuksEncryptionConfig, Field(discriminator="type")]

_UUID_PATTERN = (
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class MountConfig(ConfigModel):
    """Mount management config for a volume.

    When present, nbkp manages the volume's mount lifecycle:
    detect drive → (unlock LUKS if encrypted) → mount → backup → umount → (lock LUKS).
    """

    device_uuid: str = Field(
        ...,
        min_length=1,
        pattern=_UUID_PATTERN,
        description=(
            "Device UUID for drive detection (from ``/dev/disk/by-uuid/``)."
            " For encrypted volumes: the LUKS container UUID (from crypttab/blkid);"
            " the unlocked device is named ``luks-<uuid>`` by udisks unless the"
            " LUKS2 header carries a label or a crypttab entry overrides it"
            " (nbkp discovers the actual name either way)."
            " For unencrypted volumes: the filesystem UUID (from fstab/blkid)."
        ),
    )
    encryption: EncryptionConfig | None = Field(
        default=None,
        description="Encryption config. Omit for unencrypted volumes.",
    )


class LocalVolume(ConfigModel):
    type: Literal["local"] = "local"
    slug: Slug
    path: str | None = Field(
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
    mount: MountConfig | None = Field(
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

    @field_validator("path", mode="after")
    @classmethod
    def _require_absolute_path(cls, v: str | None) -> str | None:
        # A relative path would resolve against whichever directory nbkp is
        # started from, so the volume would move between a shell and a timer.
        if v is not None and not Path(v).is_absolute():
            raise config_error(
                ConfigValidationErrorKind.RELATIVE_PATH,
                f"path must be absolute (or start with ~), got {v!r}",
            )
        return v

    @model_validator(mode="after")
    def _require_path_without_mount(self) -> LocalVolume:
        _validate_path_requirement(self)
        return self


class RemoteVolume(ConfigModel):
    type: Literal["remote"] = "remote"
    slug: Slug
    ssh_endpoint: str = Field(
        ..., min_length=1, description="Primary SSH endpoint slug"
    )
    ssh_endpoints: list[str] | None = Field(
        default=None, description="Candidate endpoints for auto-selection"
    )
    path: str | None = Field(
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
    mount: MountConfig | None = Field(
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

    @property
    def endpoint_candidates(self) -> list[str]:
        """The endpoints resolution picks from, in order: ``ssh-endpoints``
        when set (``ssh-endpoint`` is then not a candidate), else the
        primary alone."""
        return list(self.ssh_endpoints) if self.ssh_endpoints else [self.ssh_endpoint]

    @model_validator(mode="after")
    def _require_path_without_mount(self) -> RemoteVolume:
        _validate_path_requirement(self)
        return self


def _validate_path_requirement(volume: LocalVolume | RemoteVolume) -> None:
    """Mount-managed volumes may omit ``path`` (the mountpoint is discovered at
    runtime). Externally-mounted volumes must declare ``path`` — it is their
    only locator.
    """
    if volume.mount is None and volume.path is None:
        msg = (
            f"volume '{volume.slug}': 'path' is required for volumes without a"
            " 'mount' section"
        )
        raise config_error(ConfigValidationErrorKind.PATH_REQUIRED, msg)


Volume = Annotated[LocalVolume | RemoteVolume, Field(discriminator="type")]


def join_subdir(base: str, subdir: str | None) -> str:
    return f"{base}/{subdir}" if subdir else base


def resolve_endpoint_path(
    volume: LocalVolume | RemoteVolume, subdir: str | None
) -> str:
    """The volume's ``path`` must be resolved by this point — either declared in
    config, or filled in from the discovered mountpoint after mounting (see
    ``disks.observation.apply_effective_paths``); ``ValueError`` otherwise.
    """
    if volume.path is None:
        msg = f"volume '{volume.slug}': mount path not resolved"
        raise ValueError(msg)
    return join_subdir(volume.path, subdir)
