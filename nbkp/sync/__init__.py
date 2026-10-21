"""Sync execution: rsync command building, orchestration, and per-mode runs."""

from .rsync import ProgressMode as ProgressMode
lazy from .orchestration import run_all_syncs as run_all_syncs
lazy from .results import (
    SyncFailureKind as SyncFailureKind,
    SyncOutcome as SyncOutcome,
    SyncResult as SyncResult,
    SyncWarning as SyncWarning,
    SyncWarningKind as SyncWarningKind,
    result_severity as result_severity,
)
