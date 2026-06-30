# Building and Testing

The unit tests cover the core logic of the tool. Integration and end-to-end tests exercise the real rsync/SSH/btrfs pipeline against Docker containers and local filesystems. See [Testing Strategy](guidelines.md#testing-strategy) for details on each test category.

Additionally, `nbkp demo` (or `nbkp-demo`) provides helpers for manual testing/QA. The `seed --docker` command requires the `docker` extra: `pipx install nbkp[docker]`. In dev: `poetry run nbkp demo`.

Run automated tests and checks (no external dependencies):
```bash
# mise tasks
mise run check              # Run all checks: format + lint + type-check + lock-check + clidocs-check + configdocs-check + depgraph-check
mise run check-all          # Run all checks: regular checks + all tests

mise run test-all           # All tests

mise run test-unit               # Unit tests only (no Docker, no external dependencies)

mise run test-e2e                # End-to-end sync tests (Docker + local)
mise run test-e2e-local           # End-to-end sync tests (local)
mise run test-e2e-docker           # End-to-end sync tests (Docker only)

mise run test-integration   # All integration tests (Docker + filesystem)
mise run test-integration-docker  # All Docker-based tests (e2e + integration)
mise run test-integration-docker-btrfs   # Local btrfs tests inside Docker (privileged)
mise run test-integration-fs     # Filesystem integration tests

mise run format             # ruff format
mise run lint               # ruff check
mise run type-check         # pyright
mise run compat-check       # vermin (enforce Python >=3.12 compatibility)
mise run lock-check         # check mise.lock is up to date

mise run clidocs            # regenerate CLI reference in docs/cli-reference.md
mise run clidocs-check      # check CLI reference is up to date
mise run configdocs         # regenerate config reference tables in docs/concepts.md
mise run configdocs-check   # check config reference tables in docs/concepts.md are up to date
mise run depgraph           # regenerate Module Overview in docs/architecture.md
mise run depgraph-check     # check Module Overview is up to date

# Using Poetry syntax directly
poetry run pytest tests/ --ignore=tests/e2e_docker/ --ignore=tests/integration_docker/ --ignore=tests/integration_fs/ -n auto -v  # Unit tests only
poetry run pytest tests/e2e_docker/ -v                                   # End-to-end sync tests
poetry run pytest tests/integration_fs/ -n auto -v                       # Filesystem integration tests
poetry run pytest tests/integration_docker/ -n auto -v                   # Docker integration tests
poetry run pytest tests/ -v                                             # All tests
poetry run ruff format .                                                # formatting
poetry run ruff check nbkp/ tests/                                      # linting
poetry run pyright nbkp/                                                # type-checking
poetry run vermin --target=3.12- --no-tips --no-parse-comments nbkp/ tests/  # compat check
poetry run pytest tests/test_ssh.py::TestBuildSshBaseArgs::test_full -v # run a single test
```

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

See [Testing Strategy](guidelines.md#testing-strategy) for details on test categories, automated vs manual testing, and intentionally untested areas.

The Docker-based test suites use [testcontainers](https://testcontainers-python.readthedocs.io/) and automatically:
- Generate an ephemeral SSH key pair
- Build and start a Docker container with SSH, rsync, and a btrfs filesystem
- Run the tests
- Tear down the container on completion

## Release Process
- Trigger the `release` workflow: `gh workflow run release.yml`
- Let github workflows take care of the rest
    - `release` workflow: will bump version according to conventional commit conventions, push tag, and create a Github release
    - `publish` workflow: will publish the new version to PyPI
- When this is a major release, manually:
    - include additional demo scenarios in `demo/demo.sh`
    - update `README.md` to include the latest [asciinema recording](https://asciinema.org/~samidalouche/recordings) 


## Github Config
- The `main` branch is protected against force pushes.
- Settings > Advanced Security > Enable Dependency graph
- Set up the following Github secrets:
    - `ASCIINEMA_INSTALL_ID`:
        1. Execute `asciinema auth` in your terminal
        2. Click the suggested link
        3. store the content of `~/.local/state/asciinema/install-id` in the `ASCIINEMA_INSTALL_ID` secret
- Settings > Advanced Security > Enable Dependency graph

## Renovate
- Added the [iglootools](https://github.com/iglootools) org to [developer.mend.io](https://developer.mend.io/)

