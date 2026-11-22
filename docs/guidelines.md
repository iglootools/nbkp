# Project-Specific Guidelines

What every change to nbkp must respect beyond the shared
[iglootools guidelines](https://github.com/iglootools/common/blob/main/guidelines/README.md):
where nbkp departs from them, the rules it adds, which documents each kind of
change updates, and where the conventions for particular areas live. Read it
before any change; it overrides the shared guidelines where they disagree.

<!-- toc -->
**On this page**

- [Area conventions](#area-conventions)
- [Exceptions](#exceptions)
  - [Keep the SSH timeout keys in plain seconds](#keep-the-ssh-timeout-keys-in-plain-seconds)
- [Rules](#rules)
  - [Wrap text nbkp did not author in `rich.text.Text`](#wrap-text-nbkp-did-not-author-in-richtexttext)
  - [Mirror a change to `run` or the sync logic in `sh`](#mirror-a-change-to-run-or-the-sync-logic-in-sh)
- [Testing](#testing)
- [Documents each change updates](#documents-each-change-updates)
<!-- tocstop -->

## Area conventions

Read the linked document in full before changing files under these paths.

| Paths | Conventions |
|-------|-------------|
| `nbkp/cli/`, `nbkp/*/cli/`, `nbkp/cliwiring/`, `nbkp/clidisplay/` — and adding a command or option anywhere | [cli.md](guidelines/cli.md) — output formats, config and dry-run options, shared options, and their exceptions |
| `tests/`, `nbkp/*/testkit/` | [testing.md](guidelines/testing.md) — what unit tests may call, the deciders, fakes and resources to test with |

[nbkp/preflight/guidelines.md](../nbkp/preflight/guidelines.md) holds the
checklist for adding a preflight error, or a config field a check validates.

## Exceptions

### Keep the SSH timeout keys in plain seconds

`connect-timeout`, `server-alive-interval`, `banner-timeout`, `auth-timeout` and
`channel-timeout` are numbers of seconds, with no unit in the name and no
duration type, against
[general.md › Name from the domain's vocabulary](https://github.com/iglootools/common/blob/main/guidelines/coding/general.md#name-from-the-domains-vocabulary-then-the-surfaces-conventions)'s
rule on durations.

**Why:** they are configuration keys users have written, and they mirror the
OpenSSH and Paramiko option names they map to; the unit is in each field's
description and the config reference.

- **Retire it** in a major release that accepts durations with a unit (`10s`),
  keeping a bare number as seconds.

## Rules

### Wrap text nbkp did not author in `rich.text.Text`

- Rich parses `[...]` in a plain `str` as a style tag and drops it. Never
  interpolate text nbkp did not author — an exception message, a command's
  stderr, a generated command line, a config path — into a markup string. Wrap
  it in `rich.text.Text` (or `Text.assemble` when part of the line *is* styled),
  which never parses its content. Rendering an `str` is only for markup nbkp
  wrote itself. Silent truncation is the failure mode:
  `pip install nbkp[keyring]` renders as `pip install nbkp`, and rsync's
  `... (code 23) [sender=3.4.1]` loses its tail.
- A value that feeds both the Rich table and the JSON output must be
  markup-free. `Text` satisfies both: `str(text)` yields the plain content for
  JSON.

### Mirror a change to `run` or the sync logic in `sh`

A change to the `run` command or to `nbkp/sync/` is mirrored in the script `sh`
generates, or the code says why `sh` drops it.

**Why:** `sh` is the same backup as a standalone script, and a difference nobody
wrote down reads as a bug in one of them.

- A new external tool the sync runs (`stat`, `findmnt`) gets a preflight check,
  as
  [general.md › Provide preflight checks](https://github.com/iglootools/common/blob/main/guidelines/coding/general.md#provide-preflight-checks-and-troubleshooting-commands)
  describes.

## Testing

Tests patch only in the cases
[general.md › Patch only in the three documented cases](https://github.com/iglootools/common/blob/main/guidelines/coding/general.md#patch-only-in-the-three-documented-cases-and-list-each-one)
allows, enforced as
[python-tooling.md › Enforce the patching rule](https://github.com/iglootools/common/blob/main/guidelines/project-setup/python-tooling.md#enforce-the-patching-rule)
sets up. Each patch nbkp has:

| File | What is replaced | Category | Why, and when to retire it |
|---|---|---|---|
| `tests/remote/test_ssh.py` | Fabric's `Connection` class (`patch`, `MagicMock` connections) | (a) | `run_remote_command` is the wrapper under test: argument mapping, hop closing, connection reuse and retry. A real connection needs an SSH server that authenticates and runs commands. Retire if the in-process SSH server gains public-key auth and an exec channel, or if `run_remote_command` takes a connection factory. |
| `tests/remote/test_docker_testkit.py` | `docker.from_env` (`patch`, `MagicMock` client) | (a) | Checks the port binding handed to the Docker SDK, and how a failed network cleanup is reported, without a Docker daemon. Retire if the container starters take the client as a parameter. |
| `tests/snapshots/test_common.py` | `Path.symlink_to` raising | (b) | The failure must strike after nothing has been removed and before the new `latest` exists. A read-only directory would also block the removal, so it could not catch an update that deletes `latest` before creating the new link, which this test does. |
| `tests/credentials/test_credentials.py` | `sys.modules["keyring"] = None` | (b) | The test environment installs every extra, so the missing `keyring` package can only be simulated. |
| `tests/conftest.py` | `nbkp.remote.resolution.default_ssh_config_path` | (c) | Endpoint resolution falls back to `~/.ssh/config` when no SSH config is passed, so every test would otherwise read the developer's. Retire when every caller passes its SSH config explicitly. |
| `tests/remote/test_host_key_verification.py` | `HOME` | (c) | Paramiko reads `~/.ssh/known_hosts` from the home directory, below any seam of nbkp's. |

## Documents each change updates

| A change to | Updates |
|---|---|
| a command or option | `docs/cli-reference.md` (`mise run clidocs`), `features.md`, `usage.md`, and [cli.md](guidelines/cli.md) when it adds or retires an exception |
| a config field or the config schema | `docs/config-reference.md` (`mise run configdocs`, then what `build-scripts/configdocs.py` cannot derive from the models), `features.md`, `usage.md` |
| an entity, an invariant, or a rule about ordering, availability or snapshots | `domain.md` |
| a module or the layering | `architecture.md` and the import-linter contracts, as [python.md › Module Boundaries](https://github.com/iglootools/common/blob/main/guidelines/coding/python.md#module-boundaries) describes; `mise run depgraph` |
| a workflow job | its `ci-*` task, as [mise.md › Give each workflow job one `ci-*` task](https://github.com/iglootools/common/blob/main/guidelines/project-setup/mise.md#give-each-workflow-job-one-ci--task) describes |
| a patch in a test | the [table above](#testing) |
