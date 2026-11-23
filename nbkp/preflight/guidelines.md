# Preflight Conventions

What a change to the preflight checks, or to the config schema they validate,
must update. Read it before adding a preflight error or warning kind, or a
config field a check validates.

## Update every place a preflight error reaches

A new `SshEndpointErrorKind`, `SshEndpointWarningKind`, `VolumeErrorKind`,
`SourceEndpointErrorKind`, `DestinationEndpointErrorKind`,
`DestinationEndpointWarningKind` or `SyncErrorKind` member, or a config schema
change a check validates, updates **all** of the following.

**Why:** the fix printers end in a catch-all `case _`, so a member without its
own case renders an empty or generic fix and nothing else fails.

- **Troubleshoot output** (`nbkp/preflight/output/remediation.py`): add a `case`
  in the per-layer fix printer, with actionable remediation text. Fix printers
  are pure rendering: a fix that needs a probed or generated fact (like the
  discovered cleartext mapper or the polkit rule) gets it from
  `RemediationFacts`, gathered up front in `nbkp/preflight/troubleshoot.py`.
- **Seed / demo test data** (`nbkp/preflight/testkit/troubleshoot/`): add a
  `Scenario` that exercises the new error — its config entries and its statuses
  — to the module for its area, and list it in `SCENARIOS`, so
  `nbkp-demo output` renders it. `tests/preflight/test_testkit_coverage.py`
  fails until every member is covered.
- **Inactive-errors sets** (`INACTIVE_*_ERRORS`, defined next to each error enum
  in `nbkp/preflight/status/` — `ssh.py`, `volume.py`, `endpoint.py`,
  `sync.py`): if the new error should be treated as a non-fatal skip (like
  missing sentinels), add it to its layer's set.
- **The demo CLI** (`nbkp/demo/`): generate new test data that reflects the
  change.
- **The CLI**: support the new functionality in the commands, and update their
  formatting in the package's `output.py` if necessary.
