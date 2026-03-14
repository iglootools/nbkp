"""Shared helpers for chain sync tests (run vs sh)."""

from __future__ import annotations

from pathlib import Path

from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.remote.testkit.docker import (
    LUKS_MAPPER_NAME,
    REMOTE_BACKUP_PATH,
    REMOTE_BTRFS_PATH,
    REMOTE_ENCRYPTED_PATH,
)
from nbkp.sync.testkit.seed import (
    SEED_EXCLUDE_FILTERS,
    create_seed_sentinels,
    seed_volume,
)

from tests._docker_fixtures import ssh_exec


def build_chain_config(
    tmp_path: Path,
    bastion_endpoint: SshEndpoint,
    proxied_endpoint: SshEndpoint,
    *,
    luks_uuid: str | None = None,
) -> Config:
    """Build a 6-hop chain config across local and remote volumes.

    Volumes:
      src-local-bare                — chain origin (bare source)
      stage-local-hl-snapshots      — HL dest / HL source
      stage-remote-bare             — bare dest / HL source
      stage-remote-btrfs-snapshots  — btrfs dest / btrfs source (encrypted)
      stage-remote-btrfs-bare       — bare dest / HL source
      stage-remote-hl-snapshots     — HL dest / HL source
      dst-local-bare                — chain terminus (bare dest)

    When *luks_uuid* is provided, ``stage-remote-btrfs-snapshots``
    gets a ``MountConfig`` so that ``mount_volumes`` /
    ``umount_volumes`` manage the LUKS lifecycle as part of the
    chain — same code path as ``nbkp run``.
    """
    volumes: dict[str, LocalVolume | RemoteVolume] = {
        "src-local-bare": LocalVolume(
            slug="src-local-bare",
            path=str(tmp_path / "src-local-bare"),
        ),
        "stage-local-hl-snapshots": LocalVolume(
            slug="stage-local-hl-snapshots",
            path=str(tmp_path / "stage-local-hl-snapshots"),
        ),
        "stage-remote-bare": RemoteVolume(
            slug="stage-remote-bare",
            ssh_endpoint="via-bastion",
            path=f"{REMOTE_BACKUP_PATH}/bare",
        ),
        "stage-remote-btrfs-snapshots": RemoteVolume(
            slug="stage-remote-btrfs-snapshots",
            ssh_endpoint="via-bastion",
            path=(
                REMOTE_ENCRYPTED_PATH
                if luks_uuid is not None
                else f"{REMOTE_BTRFS_PATH}/snapshots"
            ),
            mount=(
                MountConfig(
                    strategy="direct",
                    device_uuid=luks_uuid,
                    encryption=LuksEncryptionConfig(
                        mapper_name=LUKS_MAPPER_NAME,
                        passphrase_id="test-luks",
                    ),
                )
                if luks_uuid is not None
                else None
            ),
        ),
        "stage-remote-btrfs-bare": RemoteVolume(
            slug="stage-remote-btrfs-bare",
            ssh_endpoint="via-bastion",
            path=f"{REMOTE_BTRFS_PATH}/bare",
        ),
        "stage-remote-hl-snapshots": RemoteVolume(
            slug="stage-remote-hl-snapshots",
            ssh_endpoint="via-bastion",
            path=f"{REMOTE_BACKUP_PATH}/hl",
        ),
        "dst-local-bare": LocalVolume(
            slug="dst-local-bare",
            path=str(tmp_path / "dst-local-bare"),
        ),
    }

    hl = HardLinkSnapshotConfig(enabled=True)
    btrfs = BtrfsSnapshotConfig(enabled=True)

    # Sync endpoints — when a destination of one step is the source
    # of the next step, we reuse the SAME endpoint slug so the
    # dependency detector sees the link.
    sync_endpoints: dict[str, SyncEndpoint] = {
        # step-1 source: bare local origin
        "ep-src-local-bare": SyncEndpoint(
            slug="ep-src-local-bare",
            volume="src-local-bare",
        ),
        # step-1 dest / step-2 source: local HL snapshots
        "ep-stage-local-hl": SyncEndpoint(
            slug="ep-stage-local-hl",
            volume="stage-local-hl-snapshots",
            hard_link_snapshots=hl,
        ),
        # step-2 dest / step-3 source: remote bare
        "ep-stage-remote-bare": SyncEndpoint(
            slug="ep-stage-remote-bare",
            volume="stage-remote-bare",
        ),
        # step-3 dest / step-4 source: remote btrfs snapshots
        "ep-stage-remote-btrfs": SyncEndpoint(
            slug="ep-stage-remote-btrfs",
            volume="stage-remote-btrfs-snapshots",
            btrfs_snapshots=btrfs,
        ),
        # step-4 dest / step-5 source: remote btrfs bare
        "ep-stage-remote-btrfs-bare": SyncEndpoint(
            slug="ep-stage-remote-btrfs-bare",
            volume="stage-remote-btrfs-bare",
        ),
        # step-5 dest / step-6 source: remote HL snapshots
        "ep-stage-remote-hl": SyncEndpoint(
            slug="ep-stage-remote-hl",
            volume="stage-remote-hl-snapshots",
            hard_link_snapshots=hl,
        ),
        # step-6 dest: bare local terminus
        "ep-dst-local-bare": SyncEndpoint(
            slug="ep-dst-local-bare",
            volume="dst-local-bare",
        ),
    }

    syncs: dict[str, SyncConfig] = {
        # local->local, HL destination
        "step-1": SyncConfig(
            slug="step-1",
            source="ep-src-local-bare",
            destination="ep-stage-local-hl",
            filters=SEED_EXCLUDE_FILTERS,
        ),
        # local->remote (bastion), bare destination
        "step-2": SyncConfig(
            slug="step-2",
            source="ep-stage-local-hl",
            destination="ep-stage-remote-bare",
            filters=SEED_EXCLUDE_FILTERS,
        ),
        # remote->remote same-server (bastion), btrfs destination
        "step-3": SyncConfig(
            slug="step-3",
            source="ep-stage-remote-bare",
            destination="ep-stage-remote-btrfs",
            filters=SEED_EXCLUDE_FILTERS,
        ),
        # remote->remote same-server (bastion), bare dest on btrfs
        "step-4": SyncConfig(
            slug="step-4",
            source="ep-stage-remote-btrfs",
            destination="ep-stage-remote-btrfs-bare",
            filters=SEED_EXCLUDE_FILTERS,
        ),
        # remote->remote same-server (bastion), HL destination
        "step-5": SyncConfig(
            slug="step-5",
            source="ep-stage-remote-btrfs-bare",
            destination="ep-stage-remote-hl",
            filters=SEED_EXCLUDE_FILTERS,
        ),
        # remote (bastion)->local, bare destination
        "step-6": SyncConfig(
            slug="step-6",
            source="ep-stage-remote-hl",
            destination="ep-dst-local-bare",
            filters=SEED_EXCLUDE_FILTERS,
        ),
    }

    return Config(
        ssh_endpoints={
            "bastion": bastion_endpoint,
            "via-bastion": proxied_endpoint,
        },
        volumes=volumes,
        sync_endpoints=sync_endpoints,
        syncs=syncs,
    )


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
