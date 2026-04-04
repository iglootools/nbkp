"""Sync execution: rsync command building and sync runner."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .rsync import ProgressMode as ProgressMode

if TYPE_CHECKING:
    from .runner import SyncOutcome as SyncOutcome
    from .runner import SyncResult as SyncResult
    from .runner import run_all_syncs as run_all_syncs

__all__ = [
    "ProgressMode",
    "SyncOutcome",
    "SyncResult",
    "run_all_syncs",
]

_LAZY_MODULES = {
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
