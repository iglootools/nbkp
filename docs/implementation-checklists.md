# Implementation Checklists

These checklists serve as a reminder for things to check when implementing new features or making changes to the codebase. 

## Conventions
- Follow the [coding conventions](./conventions.md)

## Build and CI
- Keep Github workflows and mise tasks in sync with each other

## Documentation

In general, keep the documentation in sync with the codebase. In particular:
- **Reference documentation**: 
  - When making changes to the CLI or config schema, make sure to update the documentation in `docs/` to reflect the changes, especially: `features.md`, `usage.md` and `conventions.md`. `build-scripts/configdocs.py` sometimes needs manual additions to document aspects that cannot be derived from the models.

## Domain Logic

- When making changes to the config schema/models or preflight checks, make sure to update **all** of the following:
  - **Troubleshoot output** (`nbkp/preflight/output.py`): add a `case` in the troubleshoot match-case for every new `SyncError` or `VolumeError`, with actionable remediation text.
  - **Seed / demo test data** (`nbkp/preflight/testkit.py`): add a scenario to `troubleshoot_config` + `troubleshoot_data` that exercises the new error, so `nbkp-demo output` renders it.
  - **CLI inactive-errors set** (`nbkp/cli.py`, `_INACTIVE_ERRORS`): if the new error should be treated as a non-fatal skip (like missing sentinels), add it here.
  - The demo CLI (`nbkp/democli.py`) to generate new test data that reflects the changes.
  - The `cli` CLI app to support the new functionality, and update the formatting logic in `output.py` if necessary.
- When making changes to the `run` command and/or `sync` logic, make sure to update **all** of the following:
  - Either mirror the behavior in the `sh` command, or explicitly add comments in the codebase to describe the reasons for dropping the original functionality
  - When adding a dependency on an external tool (e.g. `stat`, `findfmt`), add a preflight check

## Tests
- Ensure that all new functionality is covered by tests according to the [testing strategy](./conventions.md#testing-strategy).