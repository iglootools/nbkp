# Project-Specific Guidelines

A set of [implementation checklists](#implementation-checklists) serve as a reminder for things to check when implementing new features or making changes to the codebase.

For general coding, Python, and tooling guidelines, see the [common guidelines](https://github.com/iglootools/common).

<!-- toc -->
**On this page**

- [Coding](#coding)
- [Implementation Checklists](#implementation-checklists)
  - [Build and CI](#build-and-ci)
  - [Documentation](#documentation)
  - [Domain Logic](#domain-logic)
  - [Tests](#tests)
<!-- tocstop -->

## Coding
- **Naming Conventions**
  - `kebab-case` for CLI commands and config keys
- **CLI**
  - Use `typer` for CLI implementation (argument parsing, formatting, etc.)
  - Provide both human-readable and JSON output formats for all commands, with human-readable as the default.
    - *Exception — `sh`:* its output is a bash script, not a report, so there is no JSON
      mode, and `-o` means `--output-file` (where to write the script) rather than the
      `--output` format selector other commands use. Renaming the flag would break existing
      invocations and cron entries for no gain in function. Retire this exception if `sh`
      gains a structured report worth emitting (e.g. the list of generated syncs), at
      which point it takes `--output human|json` and the script path moves to a
      long-only `--output-file` in a major release.
    - *Exception — `-n` in `disks`:* in `run` and `snapshots prune` (and `demo seed`)
      `-n` is `--dry-run`, but in `disks mount|umount|status` it is `--name`, so their
      `--dry-run` is long-only. `-n` meant `--name` there first; swapping it would turn
      existing `disks mount -n <volume>` invocations into silent dry runs. Retire this
      exception in the next major release by moving `--name` to a long-only flag (or
      `-V`) and giving `-n` to `--dry-run` everywhere.
    - *Exception — `demo`:* `demo seed` and `demo output` take neither `--output` nor
      `--config`. They are manual-QA helpers, not backup commands: `seed` *writes* a config
      (to `<base-dir>/config.yaml`) rather than reading one, and `output` renders the human
      formatters with built-in fake data, so human output is its whole purpose and there is no
      config for it to load. `seed`'s report is a summary for the person running the demo,
      and the one scripted caller, `demo/demo.sh`, already knows where the config lands
      because it passes `--base-dir`. Retire this exception, by giving `seed` `--output json`
      (the base directory, config path and container ports), once a script or workflow has
      to read what `seed` created; drop it if `demo` leaves the user-facing `nbkp` app for a
      development-only entry point, where these rules no longer apply.
  - Provide ability to pass a config file to all commands (except `demo`, see its
    exception above)
  - Provide a dry-run parameter for all data-mutating or long-running operations
- **Rich output**
  - Rich parses `[...]` in a plain `str` as a style tag and drops it. Never interpolate
    text nbkp did not author — an exception message, a command's stderr, a generated
    command line, a config path — into a markup string. Wrap it in `rich.text.Text`
    (or `Text.assemble` when part of the line *is* styled), which never parses its
    content. Rendering an `str` is only for markup nbkp wrote itself.
    Silent truncation is the failure mode: `pip install nbkp[keyring]` renders as
    `pip install nbkp`, and rsync's `... (code 23) [sender=3.4.1]` loses its tail.
  - A value that feeds both the Rich table and the JSON output must be markup-free.
    `Text` satisfies both: `str(text)` yields the plain content for JSON.
- **Config**
  - Perform `~` expansion for all file paths in the config
- **Testing**
  - No real rsync/ssh/btrfs calls in unit tests: they run against fakes, and the Docker-enabled integration tests cover the real interactions.
  - **Test without patching**, as the common
    [Mocks](https://github.com/iglootools/common/blob/main/guidelines/coding/general.md#mocks)
    and [Tests](https://github.com/iglootools/common/blob/main/guidelines/coding/python.md#tests)
    rules describe. nbkp's building blocks for it:
    - **Deciders**: the preflight `*Status.from_diagnostics` interpretation, and the CLI
      deciders over `PreflightResult` / `SyncResult` / mount results.
    - **Fakes**, in `tests/fakes.py`: `FakeCommandRunner` for any `run: VolumeCommandRunner`,
      `FakeRsync`, and for CLI tests a fake `Runtime` passed as
      `CliRunner.invoke(..., obj=fake_runtime(...))`. Smaller seams follow the same shape:
      `environ`, `which`, `run_process`, `has_docker`. `completed()` builds a real
      `CompletedProcess`.
    - **Real local resources**: a Python child process, a file under `tmp_path`, the
      in-process SSH server of `tests/remote/test_host_key_verification.py`.
    - **`chdir`** (`tests/conftest.py`): changes the working directory until the end of
      the test, so relative config paths are predictable.
  - **Enforced** by pytest and ruff. `tests/conftest.py` replaces the `monkeypatch`
    fixture with one that fails the test, and ruff's `TID251` bans `unittest.mock` and
    `pytest.MonkeyPatch`, the only ways left to patch. Each exception below carries
    `# noqa: TID251` on its import, with its category, and ruff's `RUF100` fails on a
    `noqa` that is no longer needed. A new exception goes in the table below, with its
    reason.

    | File | What is replaced | Category | Why, and when to retire it |
    |---|---|---|---|
    | `tests/remote/test_ssh.py` | Fabric's `Connection` class (`patch`, `MagicMock` connections) | (a) | `run_remote_command` is the wrapper under test: argument mapping, hop closing, connection reuse and retry. A real connection needs an SSH server that authenticates and runs commands. Retire if the in-process SSH server gains public-key auth and an exec channel, or if `run_remote_command` takes a connection factory. |
    | `tests/remote/test_docker_testkit.py` | `docker.from_env` (`patch`, `MagicMock` client) | (a) | Checks the port binding handed to the Docker SDK without a Docker daemon. Retire if the container starters take the client as a parameter. |
    | `tests/snapshots/test_common.py` | `Path.symlink_to` raising | (b) | The failure must strike after nothing has been removed and before the new `latest` exists. A read-only directory would also block the removal, so it could not catch an update that deletes `latest` before creating the new link, which this test does. |
    | `tests/credentials/test_credentials.py` | `sys.modules["keyring"] = None` | (b) | The test environment installs every extra, so the missing `keyring` package can only be simulated. |
    | `tests/conftest.py` | `nbkp.remote.resolution.default_ssh_config_path` | (c) | Endpoint resolution falls back to `~/.ssh/config` when no SSH config is passed, so every test would otherwise read the developer's. Retire when every caller passes its SSH config explicitly. |
    | `tests/remote/test_host_key_verification.py` | `HOME` | (c) | Paramiko reads `~/.ssh/known_hosts` from the home directory, below any seam of nbkp's. |

    The keyring backend is process-global as well; `tests/conftest.py` and the keyring
    tests choose it through `keyring.set_keyring`, the library's own API, rather than by
    patching.
  - Generate YAML test data using the Pydantic data models and `model.model_dump()` instead of hardcoding YAML strings.
    This ensures the test data is always valid and consistent with the models.

## Implementation Checklists

These checklists serve as a reminder for things to check when implementing new features or making changes to the codebase.

### Build and CI
- Keep Github workflows and mise tasks in sync with each other

### Documentation

In general, keep the documentation in sync with the codebase. In particular:
- **Reference documentation**:
  - When making changes to the CLI or config schema, make sure to update the documentation in `docs/` to reflect the changes, especially: `features.md`, `usage.md`, `config-reference.md` and `guidelines.md`. Update `domain.md` when a change alters an entity, an invariant, or a rule about ordering, availability or snapshots. `build-scripts/configdocs.py` sometimes needs manual additions to document aspects that cannot be derived from the models.

### Domain Logic

- When making changes to the config schema/models or preflight checks, make sure to update **all** of the following:
  - **Troubleshoot output** (`nbkp/preflight/output/remediation.py`): add a `case` in the per-layer fix printer for every new `SshEndpointErrorKind`, `SshEndpointWarningKind`, `VolumeErrorKind`, `SourceEndpointErrorKind`, `DestinationEndpointErrorKind` or `SyncErrorKind`, with actionable remediation text. Fix printers are pure rendering: a fix that needs a probed or generated fact (like the discovered cleartext mapper or the polkit rule) gets it from `RemediationFacts`, gathered up front in `nbkp/preflight/output/troubleshoot.py`.
  - **Seed / demo test data** (`nbkp/preflight/testkit/troubleshoot/`): add a `Scenario` that exercises the new error — its config entries and its statuses — to the module for its area, and list it in `SCENARIOS`, so `nbkp-demo output` renders it. `tests/preflight/test_testkit_coverage.py` fails until every member is covered.
  - **Inactive-errors sets** (`INACTIVE_*_ERRORS`, defined next to each error enum in `nbkp/preflight/status/` — `ssh.py`, `volume.py`, `endpoint.py`, `sync.py`): if the new error should be treated as a non-fatal skip (like missing sentinels), add it to its layer's set.
  - The demo CLI (`nbkp/demo/`) to generate new test data that reflects the changes.
  - The `cli` CLI app to support the new functionality, and update the formatting logic in `output.py` if necessary.
- When making changes to the `run` command and/or `sync` logic, make sure to update **all** of the following:
  - Either mirror the behavior in the `sh` command, or explicitly add comments in the codebase to describe the reasons for dropping the original functionality
  - When adding a dependency on an external tool (e.g. `stat`, `findmnt`), add a preflight check

### Tests
- Ensure that all new functionality is covered by tests according to the [testing strategy](building-and-testing.md#testing-strategy).
