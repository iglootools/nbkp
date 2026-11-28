"""The config models of a local or remote volume, its mount and encryption."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from nbkp.config.protocol.base import ConfigModel, Slug
from nbkp.config.protocol.errors import ConfigValidationErrorKind, config_error


class LuksEncryptionConfig(ConfigModel):
    """
    LUKS encryption of a volume.

    ``type='luks'`` serves as discriminator for future encryption backends (e.g.
    VeraCrypt, BitLocker).
    """

    type: Literal["luks"] = Field(
        default="luks",
        description="Encryption backend. Only `luks` is supported",
    )
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
    """
    Mount management config for a volume.

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
    """
    A volume on the machine nbkp runs on.

    ``path`` is required unless ``mount`` is set, and must be absolute after
    ``~`` is expanded.
    """

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
    def normalize_path(cls, v: object) -> object:
        """Return the path with ``~`` expanded and trailing slashes stripped."""
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
    """
    A volume on a host nbkp reaches through one of its SSH endpoints.

    ``path`` is required unless ``mount`` is set; it is a path on the remote
    host, so ``~`` is left for SSH and rsync to resolve.
    """

    type: Literal["remote"] = "remote"
    slug: Slug
    ssh_endpoint: str = Field(
        ...,
        min_length=1,
        description="Primary SSH endpoint slug",
    )
    ssh_endpoints: list[str] | None = Field(
        default=None,
        description="Candidate endpoints for auto-selection",
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
    def normalize_path(cls, v: object) -> object:
        """Return the path with trailing slashes stripped, keeping ``/`` itself."""
        # An empty string stays empty for min_length to reject: collapsing it
        # to "/" would point an unfilled template value at the remote root.
        match v:
            case str() if v:
                return v.rstrip("/") or "/"
            case _:
                return v

    @field_validator("path", mode="after")
    @classmethod
    def _require_absolute_path(cls, v: str | None) -> str | None:
        # ``~`` is the remote user's home, resolved by SSH and rsync; any other
        # relative path silently depends on where the remote shell starts.
        if v is not None and not v.startswith(("/", "~")):
            raise config_error(
                ConfigValidationErrorKind.RELATIVE_PATH,
                f"path must be absolute (or start with ~), got {v!r}",
            )
        return v

    @property
    def endpoint_candidates(self) -> list[str]:
        """
        Return the endpoints resolution picks from, in order.

        ``ssh-endpoints`` when set (``ssh-endpoint`` is then not a candidate), else the
        primary alone.
        """
        return list(self.ssh_endpoints) if self.ssh_endpoints else [self.ssh_endpoint]

    @model_validator(mode="after")
    def _require_path_without_mount(self) -> RemoteVolume:
        _validate_path_requirement(self)
        return self


def _validate_path_requirement(volume: LocalVolume | RemoteVolume) -> None:
    """
    Require ``path`` on a volume that is not mount-managed.

    Mount-managed volumes may omit ``path`` (the mountpoint is discovered at runtime).
    Externally-mounted volumes must declare ``path``: it is their only locator.
    """
    if volume.mount is None and volume.path is None:
        msg = (
            f"volume '{volume.slug}': 'path' is required for volumes without a"
            " 'mount' section"
        )
        raise config_error(ConfigValidationErrorKind.PATH_REQUIRED, msg)


Volume = Annotated[LocalVolume | RemoteVolume, Field(discriminator="type")]


def join_subdir(base: str, subdir: str | None) -> str:
    """Return *base* with *subdir* appended after a ``/``, or *base* if no subdir."""
    return f"{base}/{subdir}" if subdir else base


def resolve_endpoint_path(
    volume: LocalVolume | RemoteVolume,
    subdir: str | None,
) -> str:
    """
    Return the volume's path joined with the endpoint's subdirectory.

    The volume's ``path`` must be resolved by this point, either declared in config or
    filled in from the discovered mountpoint after mounting (see
    ``disks.observation.apply_effective_paths``); ``ValueError`` otherwise.
    """
    if volume.path is None:
        msg = f"volume '{volume.slug}': mount path not resolved"
        raise ValueError(msg)
    return join_subdir(volume.path, subdir)
