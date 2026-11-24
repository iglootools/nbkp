import contextlib
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import NoReturn

import pytest
from pytest import MonkeyPatch  # noqa: PT013, TID251 -- (c) see isolate_from_user_ssh_config

from nbkp.config import (
    Config,
    LocalVolume,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.config.testkit import write_config


def _raise_fd_soft_limit(target: int = 8192) -> None:
    """
    Lift this process's open-file soft limit toward ``target``.

    The Docker test suites (testcontainers' docker-py client + containers +
    SSH connections + the generated backup script's subprocess pipes) need far
    more than macOS's default soft ``RLIMIT_NOFILE`` of 256.  When ``pytest``
    is launched from a plain login shell (e.g. via ``mise run preflight``) it
    inherits that 256 and the e2e session exhausts it mid-run — the test fails
    and even pytest's tmp-dir ``cleanup_dead_symlinks`` can't ``scandir``,
    raising ``OSError: [Errno 24] Too many open files``.  A process may always
    raise its *soft* limit up to the *hard* limit, so do that here at import
    time (before any container starts) rather than depending on the ambient
    shell's ``ulimit``.
    """
    try:
        import resource  # noqa: PLC0415 -- Unix-only stdlib module
    except ImportError:  # non-Unix; not a supported test platform
        return
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    new_soft = target if hard == resource.RLIM_INFINITY else min(target, hard)
    if soft < new_soft:
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(resource.RLIMIT_NOFILE, (new_soft, hard))


_raise_fd_soft_limit()

pytest_plugins = ["tests._docker_fixtures"]


def pytest_configure() -> None:
    """
    Never let a test reach the developer's real OS keyring.

    The ``keyring`` credential provider is the default, so any command run
    through the CLI on a config with an encrypted volume prefetches its
    passphrase from the OS secret store, and on macOS that pops a Keychain
    approval dialog. Point keyring at its fail backend for the whole
    session (each xdist worker runs this hook too), in the environment for
    subprocesses as well as in this process. Tests of the keyring provider
    substitute the ``keyring`` module themselves.
    """
    os.environ["PYTHON_KEYRING_BACKEND"] = "keyring.backends.fail.Keyring"
    try:
        import keyring  # noqa: PLC0415 -- optional extra
        import keyring.backends.fail  # noqa: PLC0415 -- optional extra
    except ImportError:  # the 'keyring' extra is optional
        return
    keyring.set_keyring(keyring.backends.fail.Keyring())


@pytest.fixture(autouse=True)
def isolate_from_user_ssh_config(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[None]:
    """
    Never let a test read the developer's real ``~/.ssh/config``.

    Endpoint resolution falls back to it when no SSH config is passed in, so
    many tests calling ``resolve_all_endpoints(config)`` would otherwise pick
    up the developer's ``Host`` entries (ports, users, identity files) and
    behave differently from CI. Point the default at a file that never
    exists; tests exercising enrichment pass their own ``ssh_config``.
    """
    absent = tmp_path_factory.getbasetemp() / "no-user-ssh-config"
    with MonkeyPatch.context() as patch:
        patch.setattr("nbkp.remote.resolution.default_ssh_config_path", lambda: absent)
        yield


@pytest.fixture
def monkeypatch() -> NoReturn:
    """
    Replace pytest's fixture for every test: tests do not patch.

    Inject a fake through a parameter instead (docs/guidelines.md, Tests).
    The documented exceptions use ``pytest.MonkeyPatch``, which ruff bans
    everywhere else (TID251).
    """
    pytest.fail(
        "The monkeypatch fixture is disabled: inject a fake through a parameter"
        " instead (docs/guidelines.md, Tests).",
        pytrace=False,
    )


@pytest.fixture
def chdir() -> Iterator[Callable[[Path], None]]:
    """
    Change the working directory until the end of the test.

    Stands in for ``monkeypatch.chdir``, which went with the fixture above.
    """
    with contextlib.ExitStack() as stack:
        yield lambda path: stack.enter_context(contextlib.chdir(path))


def _sample_config() -> Config:
    return Config(
        ssh_endpoints={
            "nas-server": SshEndpoint(
                slug="nas-server",
                host="nas.example.com",
                port=5022,
                user="backup",
                key="~/.ssh/key",
            ),
        },
        volumes={
            "local-data": LocalVolume(slug="local-data", path="/mnt/data"),
            "nas": RemoteVolume(
                slug="nas",
                ssh_endpoint="nas-server",
                path="/volume1/backups",
            ),
        },
        sync_endpoints={
            "local-photos": SyncEndpoint(
                slug="local-photos",
                volume="local-data",
                subdir="photos",
            ),
            "nas-photos": SyncEndpoint(
                slug="nas-photos",
                volume="nas",
                subdir="photos-backup",
            ),
        },
        syncs={
            "photos-to-nas": SyncConfig(
                slug="photos-to-nas",
                source="local-photos",
                destination="nas-photos",
                enabled=True,
                filters=["+ *.jpg", "- *.tmp"],
                filter_file=("~/.config/nbkp/filters/photos.rules"),
            ),
        },
    )


def _sample_minimal_config() -> Config:
    return Config(
        volumes={
            "src": LocalVolume(slug="src", path="/src"),
            "dst": LocalVolume(slug="dst", path="/dst"),
        },
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
        },
        syncs={
            "s1": SyncConfig(
                slug="s1",
                source="ep-src",
                destination="ep-dst",
            ),
        },
    )


@pytest.fixture
def sample_config_file(tmp_path: Path) -> Path:
    return write_config(tmp_path, _sample_config())


@pytest.fixture
def sample_minimal_config_file(tmp_path: Path) -> Path:
    return write_config(tmp_path, _sample_minimal_config())


@pytest.fixture
def local_volume() -> LocalVolume:
    return LocalVolume(slug="local-data", path="/mnt/data")


@pytest.fixture
def ssh_endpoint() -> SshEndpoint:
    return SshEndpoint(
        slug="nas-server",
        host="nas.example.com",
        port=5022,
        user="backup",
        key="~/.ssh/key",
    )


@pytest.fixture
def ssh_endpoint_minimal() -> SshEndpoint:
    return SshEndpoint(
        slug="nas2-server",
        host="nas2.example.com",
    )


@pytest.fixture
def remote_volume() -> RemoteVolume:
    return RemoteVolume(
        slug="nas",
        ssh_endpoint="nas-server",
        path="/volume1/backups",
    )


@pytest.fixture
def remote_volume_minimal() -> RemoteVolume:
    return RemoteVolume(
        slug="nas2",
        ssh_endpoint="nas2-server",
        path="/backups",
    )


@pytest.fixture
def sample_config() -> Config:
    return _sample_config()


@pytest.fixture
def sample_minimal_config() -> Config:
    return _sample_minimal_config()
