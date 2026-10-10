"""Snapshot data models."""

from __future__ import annotations

import enum

from pydantic import BaseModel, ConfigDict, computed_field

from ..fsprotocol import Snapshot


class SnapshotSkipReason(str, enum.Enum):
    """Why a sync was left out of ``snapshots show`` / ``snapshots prune``.

    A skip is never a failure: failures go in the result's ``error``.
    """

    NO_SNAPSHOTS = "no snapshots configured"
    NO_MAX_SNAPSHOTS = "no max-snapshots limit"
    INACTIVE = "inactive"
    PREFLIGHT_ERRORS = "preflight errors ignored"
    """Infrastructure errors under ``--strictness ignore-all``."""


class PruneResult(BaseModel):
    """Result of pruning snapshots for a sync."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sync_slug: str
    deleted: tuple[str, ...]
    kept: int
    dry_run: bool
    skip_reason: SnapshotSkipReason | None = None
    error: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def skipped(self) -> bool:
        return self.skip_reason is not None


class ShowResult(BaseModel):
    """Result of showing snapshots for a sync."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sync_slug: str
    snapshot_mode: str
    snapshots: tuple[Snapshot, ...]
    latest: Snapshot | None
    max_snapshots: int | None
    skip_reason: SnapshotSkipReason | None = None
    error: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def skipped(self) -> bool:
        return self.skip_reason is not None
