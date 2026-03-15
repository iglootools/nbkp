"""Shared helpers for chain sync tests (run vs sh)."""

from __future__ import annotations

from pathlib import Path

from nbkp.config import (
    Config,
    SshEndpoint,
)
from nbkp.config.epresolution import ResolvedEndpoints
from nbkp.preflight.queries import check_directory_exists, read_symlink_target
from nbkp.preflight.snapshot_checks import check_btrfs_readonly, check_btrfs_subvolume
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
    resolved: ResolvedEndpoints,
) -> None:
    """Shared assertions for chain sync results.

    Verifies:
    - Final destination matches source (minus excluded/)
    - Snapshot artifacts on intermediate volumes
    - Sentinel handling on final destination
    """
    btrfs_vol = config.volumes["stage-remote-btrfs-snapshots"]
    hl_vol = config.volumes["stage-remote-hl-snapshots"]

    dst = tmp_path / "dst-local-bare"

    # Final destination matches source (minus excluded/)
    assert_trees_equal(src, dst, exclude_dirs={"excluded"})
    assert (src / "excluded").is_dir()
    assert not (dst / "excluded").exists()

    # HL dest (step-1): latest symlink on local-hl
    local_hl = tmp_path / "stage-local-hl-snapshots"
    assert (local_hl / "latest").is_symlink()
    local_hl_target = (local_hl / "latest").resolve()
    assert local_hl_target.is_dir(), (
        f"local-hl latest target is not a directory: {local_hl_target}"
    )
    assert_trees_equal(src, local_hl / "latest", exclude_dirs={"excluded"})

    # Btrfs dest (step-3): snapshot + latest symlink
    btrfs_link_target = read_symlink_target(
        btrfs_vol, f"{btrfs_vol.path}/latest", resolved
    )
    assert btrfs_link_target is not None, "btrfs latest symlink missing"
    assert "snapshots/" in btrfs_link_target

    # Verify btrfs latest target is a valid directory and a read-only snapshot
    btrfs_latest_abs = f"{btrfs_vol.path}/{btrfs_link_target}"
    assert check_directory_exists(btrfs_vol, btrfs_latest_abs, resolved), (
        f"btrfs latest target is not a directory: {btrfs_latest_abs}"
    )
    # Extract the subdir relative to the volume path for check_btrfs_subvolume
    btrfs_snapshot_subdir = btrfs_link_target
    assert check_btrfs_subvolume(btrfs_vol, btrfs_snapshot_subdir, resolved), (
        f"btrfs latest target is not a subvolume: {btrfs_latest_abs}"
    )
    assert check_btrfs_readonly(btrfs_vol, btrfs_latest_abs, resolved), (
        f"btrfs snapshot is not read-only: {btrfs_latest_abs}"
    )

    # HL dest (step-5): latest symlink on remote-hl
    hl_link_target = read_symlink_target(
        hl_vol, f"{REMOTE_BACKUP_PATH}/hl/latest", resolved
    )
    assert hl_link_target is not None, "remote-hl latest symlink missing"
    assert "snapshots/" in hl_link_target
    hl_latest_abs = f"{REMOTE_BACKUP_PATH}/hl/{hl_link_target}"
    assert check_directory_exists(hl_vol, hl_latest_abs, resolved), (
        f"remote-hl latest target is not a directory: {hl_latest_abs}"
    )

    # Sentinel handling on final destination
    step6 = config.syncs["step-6"]
    assert_sentinels_after_sync(step6, config, docker_ssh_endpoint)


# Re-export for convenience
from tests._docker_fixtures import (  # noqa: E402, F401
    assert_sentinels_after_sync,
)
