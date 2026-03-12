# Building and Testing

The unit tests cover the core logic of the tool. Integration and end-to-end tests exercise the real rsync/SSH/btrfs pipeline against Docker containers and local filesystems. See [Testing Categories](conventions.md#testing-categories) for details on each test category.

Additionally, `nbkp demo` (or `nbkp-demo`) provides helpers for manual testing/QA. The `seed --docker` command requires the `docker` extra: `pipx install nbkp[docker]`. In dev: `poetry run nbkp demo`.

Run automated tests and checks (no external dependencies):
```bash
# mise tasks
mise run check              # Run all checks: format + lint + type-check
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

# Using Poetry syntax directly
poetry run pytest tests/ --ignore=tests/e2e_sync_local/ --ignore=tests/e2e_sync_docker/ --ignore=tests/integration_docker/ --ignore=tests/integration_fs/ -v  # Unit tests only
poetry run pytest tests/e2e_sync_local/ tests/e2e_sync_docker/ -v             # End-to-end sync tests
poetry run pytest tests/integration_docker/ tests/integration_fs/ -v    # Integration tests
poetry run pytest tests/ -v                                             # All tests
poetry run ruff format .                                                # formatting
poetry run ruff check nbkp/ tests/                                      # linting
poetry run pyright nbkp/                                                # type-checking
poetry run vermin --target=3.12- --no-tips --no-parse-comments nbkp/ tests/  # compat check
poetry run pytest tests/test_ssh.py::TestBuildSshBaseArgs::test_full -v # run a single test
```

The Docker-based test suites use [testcontainers](https://testcontainers-python.readthedocs.io/) and automatically:
- Generates an ephemeral SSH key pair
- Builds and starts a Docker container with SSH, rsync, and a btrfs filesystem
- Runs tests covering local-to-local, local-to-remote, remote-to-local syncs, btrfs snapshots, and status checks
- Tears down the container on completion

The `check-links` workflow runs a link checker against the documentation to catch broken links.
It is scheduled to run weekly, but can also be triggered manually using `gh workflow run check-links.yml`.

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
- Set up the following Github secrets:
    - `ASCIINEMA_INSTALL_ID`:
        1. Execute `asciinema auth` in your terminal
        2. Click the suggested link
        3. store the content of `~/.local/state/asciinema/install-id` in the `ASCIINEMA_INSTALL_ID` secret


## Renovate
- Added the [iglootools](https://github.com/iglootools) org to [developer.mend.io](https://developer.mend.io/)

