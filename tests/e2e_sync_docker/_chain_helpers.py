"""Shared helpers for chain sync tests (run vs sh)."""

from __future__ import annotations

from pathlib import Path

from nbkp.config import (
    Config,
    SshEndpoint,
)
from nbkp.remote.testkit.docker import (
    REMOTE_BACKUP_PATH,
)
from nbkp.sync.testkit.seed import (
    build_chain_config,
    create_seed_sentinels,
    seed_volume,
)

from tests._docker_fixtures import ssh_exec

# Re-export so test modules can import from here
__all__ = ["build_chain_config"]


def setup_chain(
    config: Config,
    tmp_path: Path,
    docker_ssh_endpoint: SshEndpoint,
) -> Path:
    """Common setup: sentinels, seed data.

    Btrfs snapshot infrastructure (``staging/`` subvolume,
    ``snapshots/`` directory, ``latest`` symlink) is created
    by ``create_seed_sentinels``.

    Returns the source directory path.
    """

    def _run_remote(cmd: str) -> None:
        ssh_exec(docker_ssh_endpoint, cmd)

    create_seed_sentinels(config, remote_exec=_run_remote)

    # Seed data in src-local-bare only
    src_vol = config.volumes["src-local-bare"]
    seed_volume(src_vol)

    return tmp_path / "src-local-bare"


def assert_trees_equal(
    expected: Path,
    actual: Path,
    *,
    exclude_dirs: set[str] | None = None,
) -> None:
    """Assert two directory trees have identical structure and content.

    Files under *exclude_dirs* (relative dir names) are skipped in
    the expected tree — they should NOT appear in the actual tree.
    """
    _exclude = exclude_dirs or set()

    def _is_excluded(p: Path, root: Path) -> bool:
        return any(part in _exclude for part in p.relative_to(root).parts)

    expected_files = {
        p.relative_to(expected): p
        for p in sorted(expected.rglob("*"))
        if p.is_file()
        and not p.name.startswith(".nbkp-")
        and not _is_excluded(p, expected)
    }
    actual_files = {
        p.relative_to(actual): p
        for p in sorted(actual.rglob("*"))
        if p.is_file() and not p.name.startswith(".nbkp-")
    }
    assert set(expected_files) == set(actual_files), (
        f"tree mismatch:\n"
        f"  missing: {set(expected_files) - set(actual_files)}\n"
        f"  extra:   {set(actual_files) - set(expected_files)}"
    )
    for rel, exp_path in expected_files.items():
        assert actual_files[rel].read_bytes() == exp_path.read_bytes(), (
            f"content mismatch: {rel}"
        )


def assert_chain_results(
    src: Path,
    tmp_path: Path,
    config: Config,
    docker_ssh_endpoint: SshEndpoint,
) -> None:
    """Shared assertions for chain sync results.

    Verifies:
    - Final destination matches source (minus excluded/)
    - Snapshot artifacts on intermediate volumes
    - Sentinel handling on final destination
    """
    btrfs_vol = config.volumes["stage-remote-btrfs-snapshots"]

    dst = tmp_path / "dst-local-bare"

    # Final destination matches source (minus excluded/)
    assert_trees_equal(src, dst, exclude_dirs={"excluded"})
    assert (src / "excluded").is_dir()
    assert not (dst / "excluded").exists()

    # HL dest (step-1): latest symlink on local-hl
    local_hl = tmp_path / "stage-local-hl-snapshots"
    assert (local_hl / "latest").is_symlink()
    assert_trees_equal(src, local_hl / "latest", exclude_dirs={"excluded"})

    # Btrfs dest (step-3): snapshot + latest symlink
    snap_check = ssh_exec(
        docker_ssh_endpoint,
        f"ls {btrfs_vol.path}/snapshots/",
    )
    assert snap_check.stdout.strip()
    btrfs_link = ssh_exec(
        docker_ssh_endpoint,
        f"readlink {btrfs_vol.path}/latest",
    )
    assert "snapshots/" in btrfs_link.stdout

    # HL dest (step-5): latest symlink on remote-hl
    hl_check = ssh_exec(
        docker_ssh_endpoint,
        f"readlink {REMOTE_BACKUP_PATH}/hl/latest",
    )
    assert "snapshots/" in hl_check.stdout

    # Sentinel handling on final destination
    step6 = config.syncs["step-6"]
    assert_sentinels_after_sync(step6, config, docker_ssh_endpoint)


# Re-export for convenience
from tests._docker_fixtures import (  # noqa: E402, F401
    assert_sentinels_after_sync,
)
