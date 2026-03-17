"""Component integration tests: btrfs module on local filesystem.

These tests exercise the local (subprocess) code path of the btrfs
module. They require Linux with btrfs-progs installed and a btrfs
mount point.

When btrfs is unavailable (e.g. on macOS), the tests automatically
run inside a privileged Docker container instead.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

import pytest

from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    LocalVolume,
    SyncConfig,
    SyncEndpoint,
)

from tests.integration_docker._btrfs_helpers import (
    BtrfsEnv,
    run_test_creates_readonly_snapshot,
    run_test_deletes_subvolume,
    run_test_dry_run_preserves_all,
    run_test_lists_sorted_oldest_first,
    run_test_prunes_oldest_beyond_limit,
    run_test_returns_most_recent,
)

_btrfs_available = platform.system() == "Linux" and shutil.which("btrfs") is not None

_skip_no_btrfs = pytest.mark.skipif(
    not _btrfs_available,
    reason="btrfs not available (runs via Docker proxy)",
)

# The btrfs mount point — set by the Docker entrypoint or CI
BTRFS_MOUNT = os.environ.get("NBKP_BTRFS_MOUNT", "/srv/btrfs-backups")


def _make_btrfs_config(
    src_path: str,
    dst_path: str,
) -> tuple[SyncConfig, Config]:
    """Build local btrfs config."""
    src_vol = LocalVolume(slug="src", path=src_path)
    dst_vol = LocalVolume(slug="dst", path=dst_path)
    config = Config(
        volumes={"src": src_vol, "dst": dst_vol},
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-dst": SyncEndpoint(
                slug="ep-dst",
                volume="dst",
                btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
            ),
        },
        syncs={
            "test-sync": SyncConfig(
                slug="test-sync",
                source="ep-src",
                destination="ep-dst",
            ),
        },
    )
    sync = config.syncs["test-sync"]
    return sync, config


@pytest.fixture()
def btrfs_dst(tmp_path: Path) -> Path:
    """Create a destination directory on the btrfs mount.

    Uses a subdirectory of BTRFS_MOUNT to ensure btrfs ops work.
    Falls back to tmp_path if BTRFS_MOUNT doesn't exist.
    """
    mount = Path(BTRFS_MOUNT)
    if mount.is_dir():
        dst = mount / f"test-{os.getpid()}"
        dst.mkdir(exist_ok=True)
        yield dst
        # Cleanup: delete any snapshot subvolumes first
        snaps_dir = dst / "snapshots"
        if snaps_dir.is_dir():
            for snap in sorted(snaps_dir.iterdir()):
                subprocess.run(
                    [
                        "btrfs",
                        "property",
                        "set",
                        str(snap),
                        "ro",
                        "false",
                    ],
                    capture_output=True,
                )
                subprocess.run(
                    [
                        "btrfs",
                        "subvolume",
                        "delete",
                        str(snap),
                    ],
                    capture_output=True,
                )
        staging_sub = dst / "staging"
        if staging_sub.is_dir():
            subprocess.run(
                [
                    "btrfs",
                    "subvolume",
                    "delete",
                    str(staging_sub),
                ],
                capture_output=True,
            )
        shutil.rmtree(dst, ignore_errors=True)
    else:
        pytest.skip(f"btrfs mount not found: {BTRFS_MOUNT}")


@pytest.fixture()
def btrfs_env_local(tmp_path: Path, btrfs_dst: Path) -> BtrfsEnv:
    """BtrfsEnv backed by a local btrfs mount."""
    src = tmp_path / "src"
    src.mkdir()

    sync, config = _make_btrfs_config(str(src), str(btrfs_dst))

    def _create_staging() -> None:
        subprocess.run(
            ["btrfs", "subvolume", "create", str(btrfs_dst / "staging")],
            check=True,
            capture_output=True,
        )
        (btrfs_dst / "snapshots").mkdir(exist_ok=True)

    def _seed_staging(content: str) -> None:
        (btrfs_dst / "staging" / "data.txt").write_text(content)

    def _check_exists(path: str) -> bool:
        return Path(path).is_dir()

    def _check_readonly(path: str) -> bool:
        result = subprocess.run(
            ["btrfs", "property", "get", path, "ro"],
            capture_output=True,
            text=True,
        )
        return "ro=true" in result.stdout

    return BtrfsEnv(
        sync=sync,
        config=config,
        resolved={},
        create_staging=_create_staging,
        seed_staging=_seed_staging,
        check_exists=_check_exists,
        check_readonly=_check_readonly,
    )


# ── Component-level tests (delegated to shared helpers) ─────────────


@_skip_no_btrfs
class TestCreateSnapshot:
    def test_creates_readonly_snapshot(self, btrfs_env_local: BtrfsEnv) -> None:
        run_test_creates_readonly_snapshot(btrfs_env_local)


@_skip_no_btrfs
class TestListSnapshots:
    def test_lists_sorted_oldest_first(self, btrfs_env_local: BtrfsEnv) -> None:
        run_test_lists_sorted_oldest_first(btrfs_env_local)


@_skip_no_btrfs
class TestGetLatestSnapshot:
    def test_returns_most_recent(self, btrfs_env_local: BtrfsEnv) -> None:
        run_test_returns_most_recent(btrfs_env_local)


@_skip_no_btrfs
class TestDeleteSnapshot:
    def test_deletes_subvolume(self, btrfs_env_local: BtrfsEnv) -> None:
        run_test_deletes_subvolume(btrfs_env_local)


@_skip_no_btrfs
class TestPruneSnapshots:
    def test_prunes_oldest_beyond_limit(self, btrfs_env_local: BtrfsEnv) -> None:
        run_test_prunes_oldest_beyond_limit(btrfs_env_local)

    def test_dry_run_preserves_all(self, btrfs_env_local: BtrfsEnv) -> None:
        run_test_dry_run_preserves_all(btrfs_env_local)


# ── Guard: at least one execution path must be available ─────

_docker_available_cached: bool | None = None


def _docker_available() -> bool:
    """Check if Docker is available and daemon is running."""
    try:
        import docker as dockerlib

        client = dockerlib.from_env()
        client.ping()
        return True
    except Exception:
        return False


def test_btrfs_local_has_execution_path() -> None:
    """Fail loudly if neither native btrfs nor Docker is
    available — all other tests would silently skip."""
    assert _btrfs_available or _docker_available(), (
        "Cannot run btrfs-local tests: "
        "btrfs is not available and Docker is not running. "
        "Install btrfs-progs (Linux) or start Docker."
    )


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DOCKERFILE = "nbkp/remote/testkit/dockerbuild/Dockerfile.btrfs-local-test"
_IMAGE_TAG = "nbkp-btrfs-test:latest"


@pytest.mark.skipif(
    _btrfs_available,
    reason="btrfs available locally, running tests directly",
)
@pytest.mark.skipif(
    not _docker_available(),
    reason="Docker not available",
)
class TestBtrfsLocalViaDocker:
    """Proxy: re-runs the native tests inside a privileged
    Docker container when btrfs is not available locally."""

    def test_all_pass_in_docker(self) -> None:
        from testcontainers.core.image import DockerImage

        import docker as dockerlib

        image = DockerImage(
            path=str(_PROJECT_ROOT),
            tag=_IMAGE_TAG,
            dockerfile_path=_DOCKERFILE,
        )
        image.build()

        client = dockerlib.from_env()
        container = client.containers.run(
            _IMAGE_TAG,
            command=[
                "poetry",
                "run",
                "pytest",
                "tests/integration_docker/test_btrfs_local.py",
                "-v",
                "-k",
                "not ViaDocker",
            ],
            volumes={
                str(_PROJECT_ROOT): {
                    "bind": "/app",
                    "mode": "rw",
                },
                # The bind mount above exposes the host's
                # .venv inside the container.  When the
                # container's poetry sees a macOS venv it
                # considers it broken and recreates it with
                # Linux binaries — overwriting the host's
                # .venv via the shared mount.  Mounting a
                # separate volume on /app/.venv shadows the
                # host directory so the container can't
                # touch it.
                "nbkp-btrfs-test-venv": {
                    "bind": "/app/.venv",
                    "mode": "rw",
                },
            },
            environment={
                "POETRY_VIRTUALENVS_IN_PROJECT": "false",
                "POETRY_VIRTUALENVS_PATH": "/tmp/venvs",
            },
            working_dir="/app",
            privileged=True,
            detach=True,
        )
        try:
            result = container.wait(timeout=300)
            logs = container.logs().decode()
            exit_code = result["StatusCode"]
        finally:
            container.remove(force=True)

        assert exit_code == 0, f"Tests failed inside Docker:\n{logs}"
