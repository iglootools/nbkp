# Project-Specific Guidelines

A set of [implementation checklists](#implementation-checklists) serve as a
reminder for things to check when implementing new features or making changes to
the codebase.

For general coding, Python, and tooling guidelines, see the
[common guidelines](https://github.com/iglootools/common).

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

- **Naming Conventions**: the shared ones, in
  [general.md › Naming](https://github.com/iglootools/common/blob/main/guidelines/coding/general.md#naming)
  and
  [python.md › Naming](https://github.com/iglootools/common/blob/main/guidelines/coding/python.md#naming),
  with these exceptions.
  - *Exception — SSH timeouts in plain seconds:* `connect-timeout`,
    `server-alive-interval`, `banner-timeout`, `auth-timeout` and
    `channel-timeout` are numbers of seconds, with no unit in the name and no
    duration type. They are configuration keys users have written, and they
    mirror the OpenSSH and Paramiko option names they map to; the unit is in
    each field's description and the config reference. Retire this exception in
    a major release that accepts durations with a unit (`10s`), keeping a bare
    number as seconds.
- **CLI**: see [docs/guidelines/cli.md](guidelines/cli.md).
- **Rich output**
  - Rich parses `[...]` in a plain `str` as a style tag and drops it. Never
    interpolate text nbkp did not author — an exception message, a command's
    stderr, a generated command line, a config path — into a markup string. Wrap
    it in `rich.text.Text` (or `Text.assemble` when part of the line *is*
    styled), which never parses its content. Rendering an `str` is only for
    markup nbkp wrote itself. Silent truncation is the failure mode:
    `pip install nbkp[keyring]` renders as `pip install nbkp`, and rsync's
    `... (code 23) [sender=3.4.1]` loses its tail.
  - A value that feeds both the Rich table and the JSON output must be
    markup-free. `Text` satisfies both: `str(text)` yields the plain content for
    JSON.
- **Config**
  - Perform `~` expansion for all file paths in the config
- **Testing**
  - **Test without patching**, as the common
    [Mocks](https://github.com/iglootools/common/blob/main/guidelines/coding/general.md#testability)
    and
    [Tests](https://github.com/iglootools/common/blob/main/guidelines/coding/python.md#tests)
    rules describe. nbkp's building blocks for it are in
    [docs/guidelines/testing.md](guidelines/testing.md).
  - **Enforced** by pytest and ruff. `tests/conftest.py` replaces the
    `monkeypatch` fixture with one that fails the test, and ruff's `TID251` bans
    `unittest.mock` and `pytest.MonkeyPatch`, the only ways left to patch. Each
    exception below carries `# noqa: TID251` on its import, with its category,
    and ruff's `RUF100` fails on a `noqa` that is no longer needed. A new
    exception goes in the table below, with its reason.

    | File | What is replaced | Category | Why, and when to retire it |
    |---|---|---|---|
    | `tests/remote/test_ssh.py` | Fabric's `Connection` class (`patch`, `MagicMock` connections) | (a) | `run_remote_command` is the wrapper under test: argument mapping, hop closing, connection reuse and retry. A real connection needs an SSH server that authenticates and runs commands. Retire if the in-process SSH server gains public-key auth and an exec channel, or if `run_remote_command` takes a connection factory. |
    | `tests/remote/test_docker_testkit.py` | `docker.from_env` (`patch`, `MagicMock` client) | (a) | Checks the port binding handed to the Docker SDK, and how a failed network cleanup is reported, without a Docker daemon. Retire if the container starters take the client as a parameter. |
    | `tests/snapshots/test_common.py` | `Path.symlink_to` raising | (b) | The failure must strike after nothing has been removed and before the new `latest` exists. A read-only directory would also block the removal, so it could not catch an update that deletes `latest` before creating the new link, which this test does. |
    | `tests/credentials/test_credentials.py` | `sys.modules["keyring"] = None` | (b) | The test environment installs every extra, so the missing `keyring` package can only be simulated. |
    | `tests/conftest.py` | `nbkp.remote.resolution.default_ssh_config_path` | (c) | Endpoint resolution falls back to `~/.ssh/config` when no SSH config is passed, so every test would otherwise read the developer's. Retire when every caller passes its SSH config explicitly. |
    | `tests/remote/test_host_key_verification.py` | `HOME` | (c) | Paramiko reads `~/.ssh/known_hosts` from the home directory, below any seam of nbkp's. |

    The keyring backend is process-global as well; `tests/conftest.py` and the
    keyring tests choose it through `keyring.set_keyring`, the library's own
    API, rather than by patching.
  - Generate YAML test data using the Pydantic data models and
    `model.model_dump()` instead of hardcoding YAML strings. This ensures the
    test data is always valid and consistent with the models.

## Implementation Checklists

These checklists serve as a reminder for things to check when implementing new
features or making changes to the codebase.

### Build and CI

- Keep Github workflows and mise tasks in sync with each other

### Documentation

In general, keep the documentation in sync with the codebase. In particular:

- **Reference documentation**:
  - When making changes to the CLI or config schema, make sure to update the
    documentation in `docs/` to reflect the changes, especially: `features.md`,
    `usage.md`, `config-reference.md` and `guidelines.md`. Update `domain.md`
    when a change alters an entity, an invariant, or a rule about ordering,
    availability or snapshots. `build-scripts/configdocs.py` sometimes needs
    manual additions to document aspects that cannot be derived from the models.

### Domain Logic

- When making changes to the config schema/models or preflight checks, follow
  the checklist in
  [nbkp/preflight/guidelines.md](../nbkp/preflight/guidelines.md).
- When making changes to the `run` command and/or `sync` logic, make sure to
  update **all** of the following:
  - Either mirror the behavior in the `sh` command, or explicitly add comments
    in the codebase to describe the reasons for dropping the original
    functionality
  - When adding a dependency on an external tool (e.g. `stat`, `findmnt`), add a
    preflight check

### Tests

- See [docs/guidelines/testing.md](guidelines/testing.md).
