# Project-Specific Guidelines

A set of [implementation checklists](#implementation-checklists) serve as a reminder for things to check when implementing new features or making changes to the codebase.

For general coding, Python, and tooling guidelines, see the [common guidelines](https://github.com/iglootools/common).

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
  - Provide ability to pass a config file to all commands
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
  - No real rsync/ssh/btrfs calls in unit tests - use mocks instead. Docker-enabled integration tests cover the real interactions.
  - Generate YAML test data using the Pydantic data models and `model.model_dump()` instead of hardcoding YAML strings.
    This ensures the test data is always valid and consistent with the models.

## Testing Strategy

### Manual Testing

In addition to the automated tests, the `nbkp demo seed --docker` command can be used for manual testing and debugging. It generates:
1. a similar environment as the one used in the Docker-enabled tests
2. a set of pre-configured test data.

`mise run demo-record` is a great way to iterate quickly on test scenarios: it runs the `demo seed` command as well as a few test scenarios exercising different features of the tool.

The demo uses the same real udisks path as production: the Docker harness runs `dbus-daemon`, `polkitd`, and `udisksd`, installs the generated `50-nbkp.rules` polkit rule, and connects as the non-root backup user, so the demo exercises the actual `udisksctl unlock/mount/unmount/lock` lifecycle and the polkit grant — not a bypassed simplified setup.

### Automated tests

Automated tests are organized into 4 categories based on what they test and what infrastructure they require:

1. **Unit tests** (`tests/`, `tests/sync/`, `tests/remote/`, `tests/disks/`, …) — Mock all external calls (rsync, SSH, filesystem). Test logic and command building. No external dependencies.

2. **E2E (Docker)** (`tests/e2e_docker/`) — Full pipeline tests against Docker containers. The chain sync test exercises a 6-hop pipeline covering all four sync direction combinations (local→local, local→remote, remote→remote, remote→local), both snapshot modes (hard-link and btrfs), bastion/proxy-jump, LUKS-encrypted volumes, filter exclusion, topological ordering, and failure propagation. The shell script test generates and executes the equivalent standalone bash script.

3. **Integration (Docker)** (`tests/integration_docker/`) — Component-level tests against real infrastructure in Docker. Tests individual module functions (preflight observations, btrfs snapshot operations, hard-link snapshots, mount lifecycle, SSH connectivity, subdir endpoint mapping) via SSH. Includes local btrfs tests that run inside a privileged Docker container (`mise run test-btrfs-local`).

4. **Integration (filesystem)** (`tests/integration_fs/`) — Component-level tests using real local filesystem operations (local-to-local syncs with real rsync, hard-link snapshots, symlinks, inode verification, shell script generation). Runs on any OS that supports hard links.

### Known Gaps in Test Coverage

The following functionality is covered by unit tests but intentionally excluded from Docker/integration testing, with reasoning:

- **Credential providers** (keyring, prompt, env, command) — Keyring requires OS-level secret store setup (macOS Keychain, GNOME Keyring) unavailable in Docker. Prompt is interactive. Env and command providers are trivial wrappers. The passphrase delivery path is exercised end-to-end through LUKS mount tests, which pipe a real passphrase via stdin.
- **SSH agent authentication** — Requires managing an `ssh-agent` process in the test environment. All integration tests use explicit key files. The `allow_agent=False` parameter is validated via unit tests.
- **Paramiko-specific timeouts** (`banner_timeout`, `auth_timeout`, `channel_timeout`) — Would require a deliberately slow or broken SSH server. Correct flag generation is unit-tested.
- **`disabled_algorithms`** — Would require a server configured to require specific algorithms. Correct option mapping is unit-tested.
- **SSH `compress` / `server_alive_interval` behavior** — Verifying actual compression or keepalive behavior over the wire is impractical. Correct flag generation is unit-tested.
- **Per-hop connection options** — Multi-hop proxy tests use identical options on all hops. Per-hop option variation is covered by unit tests (each hop generates options independently).
- **Sync ordering** — Pure graph logic with no external commands. Topological sort and cycle detection are thoroughly unit-tested. Ordering is implicitly validated by the e2e chain sync test which asserts step execution order.
- **Shell script generation internals** — The `integration_fs` test suite already generates and executes a full backup script against real filesystems, validating the end-to-end sh path.

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
  - **Troubleshoot output** (`nbkp/preflight/output/remediation.py`): add a `case` in the per-layer fix printer for every new `SshEndpointError`, `SshEndpointWarning`, `VolumeError`, `SourceEndpointError`, `DestinationEndpointError` or `SyncError`, with actionable remediation text. Fix printers are pure rendering: a fix that needs a probed or generated fact (like the discovered cleartext mapper or the polkit rule) gets it from `RemediationFacts`, gathered up front in `nbkp/preflight/output/troubleshoot.py`.
  - **Seed / demo test data** (`nbkp/preflight/testkit.py`): add a scenario to `troubleshoot_config` + `troubleshoot_data` that exercises the new error, so `nbkp-demo output` renders it. `tests/preflight/test_testkit_coverage.py` fails until every member is covered.
  - **Inactive-errors sets** (`INACTIVE_*_ERRORS`, defined next to each error enum in `nbkp/preflight/status/` — `ssh.py`, `volume.py`, `endpoint.py`, `sync.py`): if the new error should be treated as a non-fatal skip (like missing sentinels), add it to its layer's set.
  - The demo CLI (`nbkp/demo/`) to generate new test data that reflects the changes.
  - The `cli` CLI app to support the new functionality, and update the formatting logic in `output.py` if necessary.
- When making changes to the `run` command and/or `sync` logic, make sure to update **all** of the following:
  - Either mirror the behavior in the `sh` command, or explicitly add comments in the codebase to describe the reasons for dropping the original functionality
  - When adding a dependency on an external tool (e.g. `stat`, `findfmt`), add a preflight check

### Tests
- Ensure that all new functionality is covered by tests according to the [testing strategy](#testing-strategy).
