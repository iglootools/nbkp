"""Sync orchestration and rsync command building."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .rsync import ProgressMode as ProgressMode

if TYPE_CHECKING:
    from .runner import PruneResult as PruneResult
    from .runner import SyncOutcome as SyncOutcome
    from .runner import SyncResult as SyncResult
    from .runner import run_all_syncs as run_all_syncs

__all__ = [
    "ProgressMode",
    "PruneResult",
    "SyncOutcome",
    "SyncResult",
    "run_all_syncs",
]


def __getattr__(name: str) -> object:
    if name in __all__:
        from . import runner

        globals().update(
            {
                "PruneResult": runner.PruneResult,
                "SyncOutcome": runner.SyncOutcome,
                "SyncResult": runner.SyncResult,
                "run_all_syncs": runner.run_all_syncs,
            }
        )
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
