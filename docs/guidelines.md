# Guidelines

A set of [implementation checklists](./implementation-checklists.md) serve as a reminder for things to check when implementing new features or making changes to the codebase. 

## General Coding Guidelines
- **Functional Style**:
  - Prefer functional programming style over procedural style. Use pure functions and avoid mutability when possible.
- **Code comments**: When making changes to the codebase, explain the reasoning when the implementation is non-obvious, and document any non-trivial design decisions or trade-offs that were made.
- **Charsets**: 
  - UTF-8 everywhere.
- **Time Management**
  - UTC for all timestamps
  - Do not generate the current timestamps directly inside the core logic: pass the timestamps from the higher-level functions, tests, and other entry points.
- **Mocks**
  - Prefer passing values as explicit parameters (with sensible defaults) over reading global/ambient state internally. This makes functions testable without mocking. For example, pass `now: datetime` instead of calling `datetime.now()` internally, pass `platform: str = sys.platform` instead of reading `sys.platform` internally. Tests should pass these values explicitly rather than patching modules.
- **Console Output**
  - Do not hardcode indents in strings, compute the indent at the call site
- **Version Management**
  - Pin specific versions of all dependencies or use a lock file (e.g. poetry.lock) to ensure reproducible builds and avoid issues with breaking changes in dependencies.
  
    ```bash
    # examples
    mise use --pin pipx:poetry
    ```
- **Command Line**
  - When calling external commands, build the command lines as lists of arguments instead of strings to avoid issues with quoting and escaping.
- **Testability**
  - Expose exceptions/errors as structured data classes and perform the assertions on the structured output in tests instead of matching against raw error message strings. This allows for more robust tests that are not brittle to changes in error message formatting.
- **No Silent Failures**
  - Avoid silent failures and ensure that all errors are surfaced with clear messages. This includes validating inputs and configurations early, and providing informative error messages when something goes wrong.

## General Python Coding Guidelines
- **Functional Style**:
  - Avoid mutable accumulator lists (`errors = []; errors.append(...)`). Instead, build lists as single expressions using `[*(...), *(...)]` unpacking, conditional `[item] if cond else []` fragments, and helper functions that return lists.
  - Prefer dict/list comprehensions over imperative loops for building collections.
  - When a function computes a list from multiple independent branches, compose the result by unpacking sub-expressions rather than mutating a shared list across branches.
- **String Literals**:
  - Prefer `dedent("""\...""")` multiline strings over concatenated single-line strings with `\n` escapes when the content has meaningful structure (e.g. YAML, config snippets, multi-line templates). Short single-line strings (e.g. `"key: value\n"`) are fine as-is.
- **Typing**: Use type annotations for all functions and methods, including return types. Use `pyright` for static type checking.
- **Data Classes**: 
  - All serialized model objects are frozen pydantic dataclasses, immutable once created.
  - Other data classes should also be frozen.
- **Formatting**: 
  - 88 characters (ruff default).
- **Python Version**: 3.12 (pyright and ruff target).
- **Control Flow**
  - Prefer match-case over if-elif-else chains
  - Prefer comprehensions and built-ins (map, filter) over manual loops when appropriate.
  - Avoid `continue` in loops, and prefer filtering with comprehensions or built-ins instead.
  - Prefer single-expression returns over early returns when the logic can be expressed concisely
    (e.g. `return bool(x) and all(...)` over guard clauses with `return False`).
  - Prefer explicit if/else syntax over implicit else
  - Prefer dict unpacking with a filtered comprehension over if-chains when conditionally
    including keys (e.g. `**{k: v for k, v in {...}.items() if v is not None}`).

## Framework-Specific Guidelines
### Github Workflows
- Whenever safe (i.e. not affecting production), enable `workflow_dispatch` and `repository_dispatch` to allow manual triggering of workflows from the GitHub UI or CLI, which is useful for testing and debugging.
- Use OpenID Connect (OIDC) authentication for publishing to PyPI, and set up a separate workflow for testing releases to Test PyPI. This allows testing the release and publish process without affecting the real PyPI index, and provides more detailed logs for debugging.

### New Project Setup
Guidelines to follow when setting up new projects.

#### Python Projects
- Mise + Poetry: Use poetry `virtualenvs.in-project = true` with mise `_.python.venv = { path = ".venv", create = true }` to ensure that the virtual environment is created inside the project directory and automatically activated when running commands with `mise run`. 
- Use `ruff` for linting and formatting, `pyright` for type-checking, and `vermin` for validating the desired Python version compatibility.

### IDE-Specific Guidelines

### VSCode
- Install the Ruff, Pylance, and tombi extensions

## Project-Specific Guidelines

### Coding
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

### Testing

#### Manual Testing

In addition to the automated tests, the `nbkp demo seed --docker` command can be used for manual testing and debugging. It generates:
1. a similar environment as the one used in the Docker-enabled tests
2. a set of pre-configured test data.

`mise run demo-record` is a great way to iterate quickly on test scenarios: it runs the `demo seed` command as well as a few test scenarios exercising different features of the tool.

The docker setup does not include a fully-functional systemd setup for managing mounts, so the demo command uses a simplified setup with direct 
`cryptsetup` and `mount` calls instead of the production systemd-based workflow. 
This allows testing the encrypted backup workflows end-to-end but nbkp's mount/umount logic needs to be tested manually on a real setup.

#### Automated tests

Automated tests are organized into 4 categories based on what they test and what infrastructure they require:

1. **Unit tests** (`tests/`, `tests/sync/`, `tests/remote/`, `tests/mount/`, …) — Mock all external calls (rsync, SSH, filesystem). Test logic and command building. No external dependencies.

2. **E2E (Docker)** (`tests/e2e_docker/`) — Full pipeline tests against Docker containers. The chain sync test exercises a 6-hop pipeline covering all four sync direction combinations (local→local, local→remote, remote→remote, remote→local), both snapshot modes (hard-link and btrfs), bastion/proxy-jump, LUKS-encrypted volumes, filter exclusion, topological ordering, and failure propagation. The shell script test generates and executes the equivalent standalone bash script.

3. **Integration (Docker)** (`tests/integration_docker/`) — Component-level tests against real infrastructure in Docker. Tests individual module functions (preflight observations, btrfs snapshot operations, hard-link snapshots, mount lifecycle, SSH connectivity, subdir endpoint mapping) via SSH. Includes local btrfs tests that run inside a privileged Docker container (`mise run test-btrfs-local`).

4. **Integration (filesystem)** (`tests/integration_fs/`) — Component-level tests using real local filesystem operations (local-to-local syncs with real rsync, hard-link snapshots, symlinks, inode verification, shell script generation). Runs on any OS that supports hard links.

#### Known Gaps in Test Coverage

The following functionality is covered by unit tests but intentionally excluded from Docker/integration testing, with reasoning:

- **Credential providers** (keyring, prompt, env, command) — Keyring requires OS-level secret store setup (macOS Keychain, GNOME Keyring) unavailable in Docker. Prompt is interactive. Env and command providers are trivial wrappers. The passphrase delivery path is exercised end-to-end through LUKS mount tests, which pipe a real passphrase via stdin.
- **SSH agent authentication** — Requires managing an `ssh-agent` process in the test environment. All integration tests use explicit key files. The `allow_agent=False` parameter is validated via unit tests.
- **Paramiko-specific timeouts** (`banner_timeout`, `auth_timeout`, `channel_timeout`) — Would require a deliberately slow or broken SSH server. Correct flag generation is unit-tested.
- **`disabled_algorithms`** — Would require a server configured to require specific algorithms. Correct option mapping is unit-tested.
- **SSH `compress` / `server_alive_interval` behavior** — Verifying actual compression or keepalive behavior over the wire is impractical. Correct flag generation is unit-tested.
- **Per-hop connection options** — Multi-hop proxy tests use identical options on all hops. Per-hop option variation is covered by unit tests (each hop generates options independently).
- **Sync ordering** — Pure graph logic with no external commands. Topological sort and cycle detection are thoroughly unit-tested. Ordering is implicitly validated by the e2e chain sync test which asserts step execution order.
- **Shell script generation internals** — The `integration_fs` test suite already generates and executes a full backup script against real filesystems, validating the end-to-end scriptgen path.
