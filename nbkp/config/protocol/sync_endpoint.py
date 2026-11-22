"""The config models of a sync endpoint and its snapshot settings."""

from typing import Literal

from pydantic import Field, field_validator, model_validator

from nbkp.config.protocol.base import ConfigModel, Slug
from nbkp.config.protocol.errors import ConfigValidationErrorKind, config_error

SnapshotMode = Literal["none", "btrfs", "hard-link"]


class BtrfsSnapshotConfig(ConfigModel):
    """Whether a sync endpoint keeps btrfs snapshots, and how many."""

    enabled: bool = Field(default=False, description="Enable snapshot management")
    max_snapshots: int | None = Field(
        default=None,
        ge=1,
        description="Maximum snapshots to keep (`null` = unlimited)",
    )


class HardLinkSnapshotConfig(ConfigModel):
    """Whether a sync endpoint keeps hard-link snapshots, and how many."""

    enabled: bool = Field(default=False, description="Enable snapshot management")
    max_snapshots: int | None = Field(
        default=None,
        ge=1,
        description="Maximum snapshots to keep (`null` = unlimited)",
    )


class SyncEndpoint(ConfigModel):
    """
    A reusable sync endpoint: volume + optional subdir + snapshots.

    Defined at the top level of the config under ``sync-endpoints``
    and referenced by slug from ``syncs``.  When used as a
    destination, snapshot config controls how backups are stored.
    When used as a source, snapshot config tells rsync to read
    from the ``latest/`` directory instead of the volume root.
    """

    slug: Slug
    volume: str = Field(..., min_length=1, description="Volume slug")
    subdir: str | None = Field(
        default=None,
        description=(
            "Subdirectory within the volume. Leading and trailing slashes are stripped."
        ),
    )

    @field_validator("subdir", mode="before")
    @classmethod
    def normalize_subdir(cls, v: object) -> object:
        """Return the subdir without outer slashes, or ``None`` if nothing is left."""
        # Non-strings pass through so pydantic rejects them (e.g. an unquoted
        # ``subdir: 2024``) instead of silently targeting the volume root.
        match v:
            case str():
                stripped = v.strip("/")
                return stripped or None
            case _:
                return v

    btrfs_snapshots: BtrfsSnapshotConfig = Field(
        default_factory=BtrfsSnapshotConfig,
        description="Btrfs snapshot config",
    )
    hard_link_snapshots: HardLinkSnapshotConfig = Field(
        default_factory=HardLinkSnapshotConfig,
        description="Hard-link snapshot config",
    )

    @model_validator(mode="after")
    def validate_snapshot_exclusivity(
        self,
    ) -> SyncEndpoint:
        """Raise a ``mutually-exclusive`` error if both snapshot kinds are enabled."""
        if self.btrfs_snapshots.enabled and self.hard_link_snapshots.enabled:
            raise config_error(
                ConfigValidationErrorKind.MUTUALLY_EXCLUSIVE,
                "btrfs-snapshots and hard-link-snapshots are mutually exclusive",
            )
        return self

    @property
    def snapshot_mode(
        self,
    ) -> SnapshotMode:
        """Return the enabled snapshot kind, or ``"none"`` when neither is enabled."""
        if self.btrfs_snapshots.enabled:
            return "btrfs"
        elif self.hard_link_snapshots.enabled:
            return "hard-link"
        else:
            return "none"

    @property
    def max_snapshots(self) -> int | None:
        """
        Retention limit of the enabled snapshot mode.

        ``None``: unlimited or no snapshots.
        """
        match self.snapshot_mode:
            case "btrfs":
                return self.btrfs_snapshots.max_snapshots
            case "hard-link":
                return self.hard_link_snapshots.max_snapshots
            case "none":
                return None
