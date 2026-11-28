# Project-Specific Guidelines

What every change to nbkp must respect beyond the shared
[iglootools guidelines](https://github.com/iglootools/common/blob/main/guidelines/README.md):
where the conventions for particular areas live, where nbkp departs from the
shared rules, the rules it adds, the patches its tests make, and which documents
each kind of change updates. Read it before any change; it overrides the shared
guidelines as
[applying-the-guidelines.md › Precedence](https://github.com/iglootools/common/blob/main/guidelines/applying-the-guidelines.md#precedence)
describes.

<!-- toc -->
**On this page**

- [Area conventions](#area-conventions)
- [Exceptions](#exceptions)
  - [Keep the SSH timeout keys in plain seconds](#keep-the-ssh-timeout-keys-in-plain-seconds)
  - [Keep the btrfs test image's Python on the dockerfile manager](#keep-the-btrfs-test-images-python-on-the-dockerfile-manager)
- [Rules](#rules)
  - [Wrap text nbkp did not author in `rich.text.Text`](#wrap-text-nbkp-did-not-author-in-richtexttext)
  - [Mirror a change to `run` or the sync logic in `sh`](#mirror-a-change-to-run-or-the-sync-logic-in-sh)
- [Tests](#tests)
- [Documentation](#documentation)
<!-- tocstop -->

## Area conventions

Read the linked document in full before changing files under these paths.

| Paths | Conventions |
|-------|-------------|
| `nbkp/cli/`, `nbkp/*/cli/`, `nbkp/cliwiring/`, `nbkp/theme/` — and adding a command or option anywhere | [cli.md](guidelines/cli.md) — the rules nbkp adds to the shared CLI rules, the helper modules, and the exceptions |
| `tests/`, `nbkp/*/testkit/` | [testing.md](guidelines/testing.md) — what unit tests may call, the deciders, fakes and resources to test with |

[nbkp/preflight/guidelines.md](../nbkp/preflight/guidelines.md) holds the
checklist for adding a preflight error, or a config field a check validates.

## Exceptions

### Keep the SSH timeout keys in plain seconds

Overrides
[general.md › Name from the domain's vocabulary, then the surface's conventions](https://github.com/iglootools/common/blob/main/guidelines/coding/general.md#name-from-the-domains-vocabulary-then-the-surfaces-conventions):
`connect-timeout`, `server-alive-interval`, `banner-timeout`, `auth-timeout` and
`channel-timeout` are numbers of seconds, with no unit in the name and no
duration type.

**Why:** they are configuration keys users have written, and they mirror the
OpenSSH and Paramiko option names they map to; the unit is in each field's
description and the config reference.

**Retires when:** a major release accepts durations with a unit (`10s`),
keeping a bare number as seconds.

### Keep the btrfs test image's Python on the dockerfile manager

Overrides
[dependency-automation.md › Give every copy of a pin the same datasource](https://github.com/iglootools/common/blob/main/guidelines/project-setup/dependency-automation.md#give-every-copy-of-a-pin-the-same-datasource):
the Python base image of
`nbkp/remote/testkit/dockerbuild/Dockerfile.btrfs-local-test` copies
`mise.toml`'s `[tools] python` pin, but Renovate tracks it through its
dockerfile manager. The image's uv pin follows the rule.

**Why:** the regex manager the rule calls for cannot refresh a digest, and the
base image is pinned by digest so that a repointed tag cannot change what the
btrfs tests run on. Docker Hub dates its tags, so the 14-day hold still applies;
the two copies only become eligible a few days apart, and the btrfs suite does
not need the same Python patch release as local development.

**Retires when:** Renovate can update a digest-pinned image through the
datasource of its `mise.toml` twin.

## Rules

### Wrap text nbkp did not author in `rich.text.Text`

Print text nbkp did not author (an exception message, a command's stderr, a
generated command line, a config path) as `rich.text.Text`, or `Text.assemble`
when part of the line is styled: the `Text` form of
[python.md › Print through Rich, escaping interpolated text exactly once](https://github.com/iglootools/common/blob/main/guidelines/coding/python.md#print-through-rich-escaping-interpolated-text-exactly-once),
not `rich.markup.escape`.

**Why:** most values nbkp prints also feed its JSON output, which must be
markup-free, and `str(text)` yields the plain content for it.

### Mirror a change to `run` or the sync logic in `sh`

A change to the `run` command or to `nbkp/sync/` is mirrored in the script `sh`
generates, or the code says why `sh` drops it.

**Why:** `sh` is the same backup as a standalone script, and a difference nobody
wrote down reads as a bug in one of them.

- A new external tool the sync runs (`stat`, `findmnt`) gets a preflight check,
  as
  [general.md › Provide preflight checks](https://github.com/iglootools/common/blob/main/guidelines/coding/general.md#provide-preflight-checks-and-troubleshooting-commands)
  describes.

## Tests

Tests patch only in the cases
[general.md › Patch only in the three documented cases](https://github.com/iglootools/common/blob/main/guidelines/coding/general.md#patch-only-in-the-three-documented-cases-and-list-each-one)
allows, enforced as
[python-tooling.md › Enforce the patching rule](https://github.com/iglootools/common/blob/main/guidelines/project-setup/python-tooling.md#enforce-the-patching-rule)
sets up. Each patch nbkp has:

| File | What it replaces | Category | Why | Retires when |
|---|---|---|---|---|
| `tests/remote/test_ssh.py` | Fabric's `Connection` class (`patch`, `MagicMock` connections) | (a) | `run_remote_command` is the wrapper under test: argument mapping, hop closing, connection reuse and retry. A real connection needs an SSH server that authenticates and runs commands. | the in-process SSH server gains public-key auth and an exec channel, or `run_remote_command` takes a connection factory |
| `tests/remote/test_docker_testkit.py` | `docker.from_env` (`patch`, `MagicMock` client) | (a) | Checks the port binding handed to the Docker SDK, and how a failed network cleanup is reported, without a Docker daemon. | the container starters take the client as a parameter |
| `tests/snapshots/test_common.py` | `Path.symlink_to` raising | (b) | The failure must strike after nothing has been removed and before the new `latest` exists. A read-only directory would also block the removal, so it could not catch an update that deletes `latest` before creating the new link, which this test does. | `update_latest_symlink` takes the link-creating operation as a parameter |
| `tests/credentials/test_credentials.py` | `sys.modules["keyring"] = None` | (b) | The test environment installs every extra, so the missing `keyring` package can only be simulated. | the keyring backend is loaded through a parameter that the test can give a failing loader |
| `tests/conftest.py` | `nbkp.remote.resolution.default_ssh_config_path` | (c) | Endpoint resolution falls back to `~/.ssh/config` when no SSH config is passed, so every test would otherwise read the developer's. | every caller passes its SSH config explicitly |
| `tests/remote/test_host_key_verification.py` | `HOME` | (c) | Paramiko reads `~/.ssh/known_hosts` from the home directory, below any seam of nbkp's. | the connection setup takes the `known_hosts` path as a parameter instead of letting Paramiko read the home directory |

## Documentation

| A change to | Updates |
|---|---|
| a command or option | `docs/cli-reference.md` (`mise run clidocs`), `features.md`, `usage.md`, and [cli.md](guidelines/cli.md) when it adds or retires an exception |
| a config field or the config schema | `docs/config-reference.md` (`mise run configdocs`, then what `build-scripts/configdocs.py` cannot derive from the models), `features.md`, `usage.md` |
| an entity, an invariant, or a rule about ordering, availability or snapshots | `domain.md` |
| a module or the layering | `architecture.md` and the import-linter contracts, as [python.md › Module Boundaries](https://github.com/iglootools/common/blob/main/guidelines/coding/python.md#module-boundaries) describes; `mise run depgraph` |
| a workflow job | its `ci-*` task, as [mise.md › Give each workflow job one `ci-*` task](https://github.com/iglootools/common/blob/main/guidelines/project-setup/mise.md#give-each-workflow-job-one-ci--task) describes |
| a patch in a test | the [table above](#tests) |
