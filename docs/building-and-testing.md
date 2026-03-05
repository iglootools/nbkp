# Building and Testing

The unit tests cover the core logic of the tool. Integration and end-to-end tests exercise the real rsync/SSH/btrfs pipeline against Docker containers and local filesystems. See [Testing Categories](conventions.md#testing-categories) for details on each test category.

Additionally, `nbkp demo` (or `nbkp-demo`) provides helpers for manual testing/QA. The `seed --docker` command requires the `docker` extra: `pipx install nbkp[docker]`. In dev: `poetry run nbkp demo`.

Run automated tests and checks (no external dependencies):
```bash
# mise tasks
mise run check              # Run all checks: format + lint + type-check + unit tests
mise run test               # Unit tests only (no Docker, no external dependencies)
mise run test-e2e           # End-to-end sync tests (Docker + local)
mise run test-integration   # All integration tests (Docker + filesystem)
mise run test-integration-docker  # All Docker-based tests (e2e + integration)
mise run test-integration-fs     # Filesystem integration tests
mise run test-btrfs-local   # Local btrfs tests inside Docker (privileged)
mise run test-all           # All tests
mise run format             # black
mise run lint               # flake8
mise run type-check         # mypy (strict: disallow_untyped_defs)

# Using Poetry syntax directly
poetry run pytest tests/ --ignore=tests/e2e_sync/ --ignore=tests/e2e_sync_docker/ --ignore=tests/integration_docker/ --ignore=tests/integration_fs/ -v  # Unit tests only
poetry run pytest tests/e2e_sync/ tests/e2e_sync_docker/ -v             # End-to-end sync tests
poetry run pytest tests/integration_docker/ tests/integration_fs/ -v    # Integration tests
poetry run pytest tests/ -v                                             # All tests
poetry run black .                                                      # formatting
poetry run flake8 nbkp/ tests/                                          # linting
poetry run mypy nbkp/ tests/                                            # type-checking
poetry run pytest tests/test_ssh.py::TestBuildSshBaseArgs::test_full -v # run a single test
```

The Docker-based test suites use [testcontainers](https://testcontainers-python.readthedocs.io/) and automatically:
- Generates an ephemeral SSH key pair
- Builds and starts a Docker container with SSH, rsync, and a btrfs filesystem
- Runs tests covering local-to-local, local-to-remote, remote-to-local syncs, btrfs snapshots, and status checks
- Tears down the container on completion

The `check-links` workflow runs a link checker against the documentation to catch broken links.
It is scheduled to run weekly, but can also be triggered manually using `gh workflow run check-links.yml`.

## Github Config
- The `main` branch is protected against force pushes.