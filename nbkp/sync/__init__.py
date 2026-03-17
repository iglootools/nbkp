"""Sync orchestration and rsync command building."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .rsync import ProgressMode as ProgressMode

if TYPE_CHECKING:
    from .pipeline import PipelineResult as PipelineResult
    from .pipeline import check_and_run as check_and_run
    from .pipeline import Strictness as Strictness
    from .pipeline import has_fatal_errors as has_fatal_errors
    from .pipeline import is_expected_skip as is_expected_skip
    from .runner import PruneResult as PruneResult
    from .runner import SyncOutcome as SyncOutcome
    from .runner import SyncResult as SyncResult
    from .runner import run_all_syncs as run_all_syncs

__all__ = [
    "Strictness",
    "PipelineResult",
    "ProgressMode",
    "PruneResult",
    "SyncOutcome",
    "SyncResult",
    "check_and_run",
    "has_fatal_errors",
    "is_expected_skip",
    "run_all_syncs",
]

_LAZY_MODULES = {
    "PipelineResult": "pipeline",
    "check_and_run": "pipeline",
    "Strictness": "pipeline",
    "has_fatal_errors": "pipeline",
    "is_expected_skip": "pipeline",
    "PruneResult": "runner",
    "SyncOutcome": "runner",
    "SyncResult": "runner",
    "run_all_syncs": "runner",
}


def __getattr__(name: str) -> object:
    module_name = _LAZY_MODULES.get(name)
    if module_name is not None:
        import importlib

        mod = importlib.import_module(f".{module_name}", __name__)
        value = getattr(mod, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
