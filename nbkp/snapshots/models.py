"""Snapshot data models."""

from __future__ import annotations

from pydantic import BaseModel

from ..fsprotocol import Snapshot


class PruneResult(BaseModel):
    """Result of pruning snapshots for a sync."""

    sync_slug: str
    deleted: list[str]
    kept: int
    dry_run: bool
    detail: str | None = None
    skipped: bool = False


class ShowResult(BaseModel):
    """Result of showing snapshots for a sync."""

    sync_slug: str
    snapshot_mode: str
    snapshots: list[Snapshot]
    latest: Snapshot | None
    max_snapshots: int | None
    detail: str | None = None
    skipped: bool = False
