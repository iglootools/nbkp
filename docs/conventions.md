# Conventions

A set of [implementation checklists](./implementation-checklists.md) serve as a reminder for things to check when implementing new features or making changes to the codebase. 

## General Coding Conventions
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

## General Python Coding Conventions
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

## Github Workflows
- Whenever safe (i.e. not affecting production), enable `workflow_dispatch` and `repository_dispatch` to allow manual triggering of workflows from the GitHub UI or CLI, which is useful for testing and debugging.
- Use OpenID Connect (OIDC) authentication for publishing to PyPI, and set up a separate workflow for testing releases to Test PyPI. This allows testing the release and publish process without affecting the real PyPI index, and provides more detailed logs for debugging.

## Application-Specific Coding Conventions
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

The docker setup does not include a fully-functional systemd setup for managing mounts, so the demo command uses a simplified setup with direct 
`cryptsetup` and `mount` calls instead of the production systemd-based workflow. 
This allows testing the encrypted backup workflows end-to-end but nbkp's mount/umount logic needs to be tested manually on a real setup.

### Automated tests

Automated tests are organized into 4 categories based on what they test and what infrastructure they require:

1. **Unit tests** (`tests/`, `tests/sync/`, `tests/remote/`) — Mock all external calls (rsync, SSH, filesystem). Test logic and command building. No external dependencies.

2. **E2E sync (Docker)** (`tests/e2e_docker/`) — Full sync pipeline with remote endpoints via Docker containers. Includes end-to-end btrfs and hard-link snapshot workflows, proxy jump, chained syncs, and remote-to-remote syncs.

3. **Integration (Docker)** (`tests/integration_docker/`) — Component-level tests against real infrastructure in Docker. Tests individual module functions (volume/sync checks, btrfs operations) via SSH. Includes local btrfs tests that run inside a privileged Docker container (`mise run test-btrfs-local`).

4. **Integration (filesystem)** (`tests/integration_fs/`) — Component-level tests using real local filesystem operations (local-to-local syncs with real rsync, hard-link snapshots, symlinks, inode verification). Runs on any OS that supports hard links.

### Known Gaps and Limitations

The following SSH features have unit test coverage (command building, option mapping) but are **not** tested end-to-end against real infrastructure:

- **SSH agent authentication**: Tests always use explicit key files. Agent-based auth (`ssh-agent`) is not exercised because it requires managing an agent process in the test environment. The `allow_agent` parameter is validated via the explicit-key-only test (`allow_agent=False`).
- **Paramiko-specific timeouts**: `banner_timeout`, `auth_timeout`, `channel_timeout` are only mock-tested. E2e testing would require a deliberately slow or broken SSH server.
- **`disabled_algorithms`**: Only mock-tested. E2e testing would require a server configured to require specific algorithms.
- **`compress` / `server_alive_interval`**: Tested for correct flag generation. Verifying actual compression or keepalive behavior is impractical.
- **Per-hop connection options**: Multi-hop proxy tests use identical options on all hops. Per-hop option variation is covered by unit tests (each hop generates options independently).
