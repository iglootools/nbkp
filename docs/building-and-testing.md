# Building and Testing

The unit tests cover the core logic of the tool, while the integration tests exercise the real rsync/SSH/btrfs pipeline against a Docker container.

Integration tests exercise the real rsync/SSH/btrfs pipeline against a Docker container.

Additionally, `nbkp demo` (or `nbkp-demo`) provides helpers for manual testing/QA. The `seed --docker` command requires the `docker` extra: `pipx install nbkp[docker]`. In dev: `poetry run nbkp demo`.

Run automated tests and checks (no external dependencies):
```bash
# mise tasks
mise run check              # Run all checks: format + lint + type-check + unit tests
mise run test               # Unit tests only (no Docker)
mise run test-integration   # Integration tests only (requires Docker)
mise run test-all           # Unit + integration tests
mise run format             # black
mise run lint               # flake8
mise run type-check         # mypy (strict: disallow_untyped_defs)

# Using Poetry syntax directly
poetry run pytest tests/ --ignore=tests/integration/ -v                 # Unit tests only (no Docker)
poetry run pytest tests/integration/ -v                                 # Integration tests only (requires Docker)
poetry run pytest tests/ -v                                             # Unit + integration tests
poetry run black .                                                      # formatting
poetry run flake8 nbkp/ tests/                                          # linting
poetry run mypy nbkp/ tests/                                            # type-checking
poetry run pytest tests/test_ssh.py::TestBuildSshBaseArgs::test_full -v # run a single test
```

The integration test suite uses [testcontainers](https://testcontainers-python.readthedocs.io/) and automatically:
- Generates an ephemeral SSH key pair
- Builds and starts a Docker container with SSH, rsync, and a btrfs filesystem
- Runs tests covering local-to-local, local-to-remote, remote-to-local syncs, btrfs snapshots, and status checks
- Tears down the container on completion

The `check-links` workflow runs a link checker against the documentation to catch broken links. 
It is scheduled to run weekly, but can also be triggered manually using `gh workflow run check-links.yml`.

## Github Config
- The `main` branch is protected against force pushes.