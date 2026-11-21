# Preflight Conventions

- When making changes to the config schema/models or preflight checks, make sure
  to update **all** of the following:
  - **Troubleshoot output** (`nbkp/preflight/output/remediation.py`): add a
    `case` in the per-layer fix printer for every new `SshEndpointErrorKind`,
    `SshEndpointWarningKind`, `VolumeErrorKind`, `SourceEndpointErrorKind`,
    `DestinationEndpointErrorKind`, `DestinationEndpointWarningKind` or
    `SyncErrorKind`, with actionable remediation text. Fix printers are pure
    rendering: a fix that needs a probed or generated fact (like the discovered
    cleartext mapper or the polkit rule) gets it from `RemediationFacts`,
    gathered up front in `nbkp/preflight/output/troubleshoot.py`.
  - **Seed / demo test data** (`nbkp/preflight/testkit/troubleshoot/`): add a
    `Scenario` that exercises the new error — its config entries and its
    statuses — to the module for its area, and list it in `SCENARIOS`, so
    `nbkp-demo output` renders it. `tests/preflight/test_testkit_coverage.py`
    fails until every member is covered.
  - **Inactive-errors sets** (`INACTIVE_*_ERRORS`, defined next to each error
    enum in `nbkp/preflight/status/` — `ssh.py`, `volume.py`, `endpoint.py`,
    `sync.py`): if the new error should be treated as a non-fatal skip (like
    missing sentinels), add it to its layer's set.
  - The demo CLI (`nbkp/demo/`) to generate new test data that reflects the
    changes.
  - The `cli` CLI app to support the new functionality, and update the
    formatting logic in `output.py` if necessary.
