"""Snapshot data models."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class PruneResult(BaseModel):
    """Result of pruning snapshots for a sync."""

    sync_slug: str
    deleted: list[str]
    kept: int
    dry_run: bool
    detail: Optional[str] = None
    skipped: bool = False
