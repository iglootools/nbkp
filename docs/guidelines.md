# Project-Specific Guidelines

A set of [implementation checklists](./implementation-checklists.md) serve as a reminder for things to check when implementing new features or making changes to the codebase.

For general coding, Python, and tooling guidelines, see the [common guidelines](https://github.com/iglootools/common-guidelines).

## Coding
- **Naming Conventions**
  - `kebab-case` for CLI commands and config keys
- **CLI**
  - Use `typer` for CLI implementation (argument parsing, formatting, etc.)
  - Provide both human-readable and JSON output formats for all commands, with human-readable as the default.
  - Provide ability to pass a config file to all commands
  - Provide a dry-run parameter for all data-mutating or long-running operations
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

The docker setup does not include a fully-functional systemd setup for managing mounts, so the demo command uses a simplified setup with direct
`cryptsetup` and `mount` calls instead of the production systemd-based workflow.
This allows testing the encrypted backup workflows end-to-end but nbkp's mount/umount logic needs to be tested manually on a real setup.

### Automated tests

Automated tests are organized into 4 categories based on what they test and what infrastructure they require:

1. **Unit tests** (`tests/`, `tests/sync/`, `tests/remote/`, `tests/mount/`, …) — Mock all external calls (rsync, SSH, filesystem). Test logic and command building. No external dependencies.

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
- **Shell script generation internals** — The `integration_fs` test suite already generates and executes a full backup script against real filesystems, validating the end-to-end scriptgen path.
