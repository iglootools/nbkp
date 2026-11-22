"""Running syncs: rsync into each destination, then snapshot and prune."""

from nbkp.sync.rsync import ProgressMode as ProgressMode
lazy from nbkp.sync.orchestration import (
    RunOptions as RunOptions,
    run_all_syncs as run_all_syncs,
)
lazy from nbkp.sync.results import (
    SyncFailureKind as SyncFailureKind,
    SyncOutcome as SyncOutcome,
    SyncResult as SyncResult,
    SyncWarning as SyncWarning,
    SyncWarningKind as SyncWarningKind,
    result_severity as result_severity,
)
