"""Sync execution: rsync command building, orchestration, and per-mode runs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .rsync import ProgressMode as ProgressMode

if TYPE_CHECKING:
    from .orchestration import run_all_syncs as run_all_syncs
    from .results import (
        SyncFailureKind as SyncFailureKind,
        SyncOutcome as SyncOutcome,
        SyncResult as SyncResult,
        SyncWarning as SyncWarning,
        SyncWarningKind as SyncWarningKind,
        result_severity as result_severity,
    )

__all__ = [
    "ProgressMode",
    "SyncFailureKind",
    "SyncOutcome",
    "SyncResult",
    "SyncWarning",
    "SyncWarningKind",
    "result_severity",
    "run_all_syncs",
]

_LAZY_MODULES = {
    "SyncFailureKind": "results",
    "SyncOutcome": "results",
    "SyncResult": "results",
    "SyncWarning": "results",
    "SyncWarningKind": "results",
    "result_severity": "results",
    "run_all_syncs": "orchestration",
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
