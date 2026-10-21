# Building and Testing

The unit tests cover the core logic of the tool. Integration and end-to-end tests exercise the real rsync/SSH/btrfs pipeline against Docker containers and local filesystems. See [Testing Strategy](#testing-strategy) for details on each test category.

Additionally, `nbkp demo` (or `nbkp-demo`) provides helpers for manual testing/QA. The `seed --docker` command requires the `docker` extra: `uv tool install 'nbkp[docker]'`. In dev, run it from the project environment: `uv run nbkp demo seed` (or `uv run nbkp demo output`).

Run automated tests and checks (no external dependencies):
```bash
# mise tasks
mise run check              # Run all checks: format-check + lint + type-check + lock-check + lock-check-uv + clidocs-check + configdocs-check + depgraph-check + boundaries-check
mise run check-all          # Run all checks: regular checks + all tests

mise run test-all           # All tests

mise run test-unit               # Unit tests only (no Docker, no external dependencies)

mise run test-e2e                # End-to-end sync tests (Docker)
mise run test-e2e-docker           # End-to-end sync tests (Docker only)

mise run test-integration   # All integration tests (Docker + filesystem)
mise run test-integration-docker  # All Docker-based tests (e2e + integration)
mise run test-integration-docker-btrfs   # Local btrfs tests inside Docker (privileged)
mise run test-integration-fs     # Filesystem integration tests

mise run format             # ruff format (rewrites files)
mise run format-check       # ruff format --check (verifies only; what check runs)
mise run lint               # ruff check
mise run type-check         # pyright
mise run lock-check         # check mise.lock is up to date
mise run lock-check-uv      # check uv.lock is consistent with pyproject.toml

mise run install            # sync .venv with uv.lock (rarely needed by hand — see below)
mise run reinstall          # delete .venv and sync from scratch

mise run clidocs            # regenerate CLI reference in docs/cli-reference.md
mise run clidocs-check      # check CLI reference is up to date
mise run configdocs         # regenerate config reference tables in docs/config-reference.md
mise run configdocs-check   # check config reference tables in docs/config-reference.md are up to date
mise run depgraph           # regenerate Module Overview in docs/architecture.md
mise run depgraph-check     # check Module Overview is up to date
mise run boundaries-check   # check the module layering rules (import-linter)

# Running tools directly
uv run pytest tests/ --ignore=tests/e2e_docker/ --ignore=tests/integration_docker/ --ignore=tests/integration_fs/ -n auto -v  # Unit tests only
uv run pytest tests/e2e_docker/ -v                                   # End-to-end sync tests
uv run pytest tests/integration_fs/ -n auto -v                       # Filesystem integration tests
uv run pytest tests/integration_docker/ -n auto -v                   # Docker integration tests
uv run pytest tests/ -v                                              # All tests
uv run ruff format .                                                 # formatting
uv run ruff check nbkp/ tests/                                       # linting
uv run pyright nbkp/                                                 # type-checking
uv run pytest tests/test_ssh.py::TestBuildSshBaseArgs::test_full -v  # run a single test
```

`uv run` is the prefix to use rather than a bare `pytest`/`ruff`. It resolves the
environment from the project root, so it is correct regardless of what is on `PATH` — a bare
command in a shell that has *another* project's `.venv` active silently runs that project's
copy of the tool. The mise tasks above use `uv run --no-sync` for the same reason.

Dependencies install themselves: `[deps.uv]` in `mise.toml` runs `uv sync --all-extras` before
any `mise run` whenever `uv.lock` or `pyproject.toml` has changed, or `.venv` has gone missing.
So `mise run install` is seldom needed explicitly, and `mise run reinstall` is for the case that
automatic check cannot see — a `.venv` that is *dirty* rather than *stale*, e.g. after a manual
`uv pip install`. Add or remove dependencies with `uv add` / `uv remove`.

### Python versions

nbkp supports exactly one Python version, the one pinned in `mise.toml` (`[tools] python`):
local development, CI and the `requires-python` floor all use it, so there is no separate
compatibility check. Raising it means moving `[tools] python` (then `mise lock`),
`requires-python`, ruff's `target-version`, pyright's `pythonVersion`, and the base image in
`nbkp/remote/testkit/dockerbuild/Dockerfile.btrfs-local-test` together. See the
[Python Version Policy](https://github.com/iglootools/common/blob/main/guidelines/coding/python.md#python-version-policy)
for the rationale.

### Parallel test execution

Tests run under [pytest-xdist](https://pytest-xdist.readthedocs.io/), with per-suite worker counts chosen for what each suite can safely parallelize:

| Suite | mise task | Workers | Why |
|---|---|---|---|
| Unit | `test-unit` | `-n auto` | Fully isolated (`tmp_path`, `monkeypatch`); no external state. |
| Filesystem integration | `test-integration-fs` | `-n auto` | Each test isolates real-rsync work under a unique `tmp_path`. |
| Docker integration | `test-integration-docker` | `-n auto` | One privileged container per worker; testkit isolates shared kernel resources — see below. |
| Local btrfs (privileged) | `test-integration-docker-btrfs` | serial | Single privileged container; real btrfs ops share one loopback mount. |
| End-to-end | `test-e2e` | serial | A few heavy multi-container chain-pipeline tests; value is in the single ordered pipeline, so parallelism only multiplies container overhead. |

**How Docker integration parallelizes safely:** session-scoped container fixtures are instantiated **once per xdist worker**, so each worker spins up its own privileged container. All those containers share the host VM kernel's **loop-device pool** and **device-mapper namespace** — naively concurrent containers contend on them (loop-device exhaustion, device-mapper name collisions, and a global `losetup -D` that would detach siblings' devices). The testkit ([`entrypoint.sh`](../nbkp/remote/testkit/dockerbuild/entrypoint.sh), [`setup-luks.sh`](../nbkp/remote/testkit/dockerbuild/setup-luks.sh), and the `luks_mapper_name` fixture) keeps them independent:

- **Per-worker-unique LUKS mapper names.** Each worker's container gets a unique `/dev/mapper/<name>` (passed via env, persisted to a file because `sudo`/SSH strip the environment, and read by `setup-luks.sh`). Avoids "device already exists" collisions on the shared dm namespace.
- **Pre-created loop-node pool.** The VM pre-populates only `/dev/loop0..14` and there is no udev in the container to create more, so a pool of nodes is `mknod`-ed at startup. Without it, once the shared pool is busy `losetup` allocates a higher number whose `/dev` node does not exist (`device node is lost`).
- **No loop-device leaks.** File-backed btrfs is mounted with `mount -o loop` (autoclear → frees on container stop). The encrypted-volume LUKS image needs an explicit `losetup` (for a stable `/dev/disk/by-uuid` entry), which is *not* autoclear; the `docker_container` fixture detaches it on teardown — matched by backing-file inode so concurrent siblings are untouched — so loops do not accumulate across runs.
- **No destructive global cleanup.** Startup never runs a blanket `losetup -D` (it would yank live siblings' devices); it only detaches loops whose backing file is `(deleted)`, as a best-effort reap of orphans from killed runs.

Within a worker, the autouse `_cleanup_remote` fixture scrubs shared remote paths between tests, keeping them independent.

To debug a flaky test in isolation, run a single process by passing `-n0` (or omitting `-n`).

The `check-links` workflow runs a link checker against the documentation to catch broken links.
It is scheduled to run weekly, but can also be triggered manually using `gh workflow run check-links.yml`.

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

3. **Integration (Docker)** (`tests/integration_docker/`) — Component-level tests against real infrastructure in Docker. Tests individual module functions (preflight observations, btrfs snapshot operations, hard-link snapshots, mount lifecycle, SSH connectivity, subdir endpoint mapping) via SSH. Includes local btrfs tests that run inside a privileged Docker container (`mise run test-integration-docker-btrfs`).

4. **Integration (filesystem)** (`tests/integration_fs/`) — Component-level tests using real local filesystem operations (local-to-local syncs with real rsync, hard-link snapshots, symlinks, inode verification, shell script generation). Runs on any OS that supports hard links.

The Docker-based test suites use [testcontainers](https://testcontainers-python.readthedocs.io/) and automatically:
- Generate an ephemeral SSH key pair
- Build and start a Docker container with SSH, rsync, and a btrfs filesystem
- Run the tests
- Tear down the container on completion

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

## Release Process
- Trigger the `release` workflow: `gh workflow run release.yml`
- Let github workflows take care of the rest
    - `release` workflow: will bump version according to conventional commit conventions, push tag, and create a Github release
    - `publish` workflow: will publish the new version to PyPI
- When this is a major release, manually:
    - include additional demo scenarios in `demo/demo.sh`
    - update `README.md` to include the latest [asciinema recording](https://asciinema.org/~samidalouche/recordings) 


## GitHub Config
- **Protect `main` and the release tags with rulesets.** Configured as the shared
  [repository conventions](https://github.com/iglootools/common/blob/main/guidelines/project-setup/repository.md#protect-the-default-branch-and-release-tags)
  describe: `main` cannot be deleted or force-pushed, changes go through a pull request
  merged with a merge commit, and the `test` check must pass first; `v*` tags cannot be
  deleted or moved, so a published version keeps pointing at the commit it was built from.

  ```bash
  gh api --method POST repos/iglootools/nbkp/rulesets --input - <<'JSON'
  {
    "name": "main",
    "target": "branch",
    "enforcement": "active",
    "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
    "rules": [
      { "type": "deletion" },
      { "type": "non_fast_forward" },
      { "type": "pull_request", "parameters": {
          "required_approving_review_count": 0,
          "dismiss_stale_reviews_on_push": false,
          "require_code_owner_review": false,
          "require_last_push_approval": false,
          "required_review_thread_resolution": false,
          "allowed_merge_methods": ["merge"] } },
      { "type": "required_status_checks", "parameters": {
          "strict_required_status_checks_policy": false,
          "do_not_enforce_on_create": false,
          "required_status_checks": [{ "context": "test", "integration_id": 15368 }] } }
    ]
  }
  JSON

  gh api --method POST repos/iglootools/nbkp/rulesets --input - <<'JSON'
  {
    "name": "release tags",
    "target": "tag",
    "enforcement": "active",
    "conditions": { "ref_name": { "include": ["refs/tags/v*"], "exclude": [] } },
    "rules": [{ "type": "deletion" }, { "type": "update" }]
  }
  JSON
  ```

  To change an existing ruleset, `PUT` the same payload to
  `repos/iglootools/nbkp/rulesets/<id>` (ids from `gh api repos/iglootools/nbkp/rulesets`).
  Verify what GitHub actually enforces — an `active` ruleset with an empty `include` list
  protects nothing and looks enabled all the same:

  ```bash
  gh api repos/iglootools/nbkp/rules/branches/main -q '[.[].type]'
  # expect: ["deletion","non_fast_forward","pull_request","required_status_checks"]
  ```

  `test` must keep running on every pull request (no `paths` filter on its workflow), or a
  pull request it skips can never be merged.
- Settings > Advanced Security > Enable Dependency graph
- Set up the following Github secrets:
    - `ASCIINEMA_INSTALL_ID`:
        1. Execute `asciinema auth` in your terminal
        2. Click the suggested link
        3. store the content of `~/.local/state/asciinema/install-id` in the `ASCIINEMA_INSTALL_ID` secret
- **Enable Dependabot security updates.** These are *repository settings*, and
  [.github/dependabot.yml](../.github/dependabot.yml) cannot switch them on — that file only
  shapes the resulting PRs and restricts Dependabot to the security half
  (`open-pull-requests-limit: 0`, because Renovate owns routine version updates and delays them
  14 days). A repo with the file committed and these settings off looks configured while
  watching nothing:

    ```bash
    gh api -X PUT repos/iglootools/nbkp/vulnerability-alerts
    gh api -X PUT repos/iglootools/nbkp/automated-security-fixes
    ```

    Both are required, and they are separate switches: `vulnerability-alerts` is what notices a
    vulnerable dependency, `automated-security-fixes` is what opens the fix PR. Alerts alone
    give a Security tab entry and no PR — the easy state to land in by accident, since alerts
    are the more discoverable of the two.

    Each `PUT` returns `204 No Content` and prints nothing, so verify rather than assume:

    ```bash
    gh api repos/iglootools/nbkp/vulnerability-alerts -i | head -1   # expect HTTP/2.0 204
    gh api repos/iglootools/nbkp/automated-security-fixes            # expect "enabled": true
    ```

    A `404` from the first is the disabled state, not a missing endpoint. Undo with the same two
    URLs and `-X DELETE`. Needs admin on the repository; the dependency graph is a prerequisite
    and is on by default for public repositories.

## Renovate
- Added the [iglootools](https://github.com/iglootools) org to [developer.mend.io](https://developer.mend.io/)

