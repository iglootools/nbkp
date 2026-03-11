"""Sync endpoint model: volume + optional subdir + snapshot config."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import ConfigDict, Field, field_validator, model_validator

from .base import Slug, _BaseModel


class BtrfsSnapshotConfig(_BaseModel):
    """Configuration for btrfs snapshot management."""

    model_config = ConfigDict(frozen=True)
    enabled: bool = False
    max_snapshots: Optional[int] = Field(default=None, ge=1)


class HardLinkSnapshotConfig(_BaseModel):
    """Configuration for hard-link-based snapshot management."""

    model_config = ConfigDict(frozen=True)
    enabled: bool = False
    max_snapshots: Optional[int] = Field(default=None, ge=1)


class SyncEndpoint(_BaseModel):
    """A reusable sync endpoint: volume + optional subdir + snapshots.

    Defined at the top level of the config under ``sync-endpoints``
    and referenced by slug from ``syncs``.  When used as a
    destination, snapshot config controls how backups are stored.
    When used as a source, snapshot config tells rsync to read
    from the ``latest/`` directory instead of the volume root.
    """

    slug: Slug
    volume: str = Field(..., min_length=1)
    subdir: Optional[str] = None

    @field_validator("subdir", mode="before")
    @classmethod
    def normalize_subdir(cls, v: Any) -> str | None:
        if not isinstance(v, str):
            return None
        stripped = v.strip("/")
        return stripped if stripped else None

    btrfs_snapshots: BtrfsSnapshotConfig = Field(
        default_factory=lambda: BtrfsSnapshotConfig()
    )
    hard_link_snapshots: HardLinkSnapshotConfig = Field(
        default_factory=lambda: HardLinkSnapshotConfig()
    )

    @model_validator(mode="after")
    def validate_snapshot_exclusivity(
        self,
    ) -> SyncEndpoint:
        if self.btrfs_snapshots.enabled and self.hard_link_snapshots.enabled:
            raise ValueError(
                "btrfs-snapshots and hard-link-snapshots are mutually exclusive"
            )
        return self

    @property
    def snapshot_mode(
        self,
    ) -> Literal["none", "btrfs", "hard-link"]:
        if self.btrfs_snapshots.enabled:
            return "btrfs"
        elif self.hard_link_snapshots.enabled:
            return "hard-link"
        else:
            return "none"
