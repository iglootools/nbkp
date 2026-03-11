"""Snapshot configuration models: btrfs and hard-link."""

from __future__ import annotations

from typing import Optional

from pydantic import ConfigDict, Field

from .base import _BaseModel


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
