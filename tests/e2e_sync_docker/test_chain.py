"""Integration test: end-to-end chain sync pipeline.

Verifies data propagates through a 6-hop chain using all
supported sync variants and snapshot modes, with bastion SSH
for all remote access:

  src-local-bare → stage-local-hl-snapshots →
    stage-remote-bare → stage-remote-btrfs-snapshots →
    stage-remote-btrfs-bare → stage-remote-hl-snapshots →
    dst-local-bare
"""

from __future__ import annotations

from pathlib import Path

from nbkp.check import check_all_syncs
from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
    resolve_all_endpoints,
)
from nbkp.sync.runner import run_all_syncs
from nbkp.testkit.docker import (
    REMOTE_BACKUP_PATH,
    REMOTE_BTRFS_PATH,
)
from nbkp.testkit.gen.fs import create_seed_sentinels, seed_volume

from tests._docker_fixtures import assert_sentinels_after_sync, ssh_exec

BTRFS_SNAPSHOTS_PATH = f"{REMOTE_BTRFS_PATH}/snapshots"
BTRFS_BARE_PATH = f"{REMOTE_BTRFS_PATH}/bare"


def _build_chain_config(
    tmp_path: Path,
    bastion_endpoint: SshEndpoint,
    proxied_endpoint: SshEndpoint,
) -> Config:
    """Build a 6-hop chain config across local and remote volumes.

    Volumes:
      src-local-bare                — chain origin (bare source)
      stage-local-hl-snapshots      — HL dest / HL source
      stage-remote-bare             — bare dest / HL source
      stage-remote-btrfs-snapshots  — btrfs dest / btrfs source
      stage-remote-btrfs-bare       — bare dest / HL source
      stage-remote-hl-snapshots     — HL dest / HL source
      dst-local-bare                — chain terminus (bare dest)
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
            path=BTRFS_SNAPSHOTS_PATH,
        ),
        "stage-remote-btrfs-bare": RemoteVolume(
            slug="stage-remote-btrfs-bare",
            ssh_endpoint="via-bastion",
            path=BTRFS_BARE_PATH,
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

    hl_src = HardLinkSnapshotConfig(enabled=True)
    hl_dst = HardLinkSnapshotConfig(enabled=True)
    btrfs_src = BtrfsSnapshotConfig(enabled=True)
    btrfs_dst = BtrfsSnapshotConfig(enabled=True)

    syncs: dict[str, SyncConfig] = {
        # local→local, HL destination
        "step-1": SyncConfig(
            slug="step-1",
            source=SyncEndpoint(volume="src-local-bare"),
            destination=SyncEndpoint(
                volume="stage-local-hl-snapshots",
                hard_link_snapshots=hl_dst,
            ),
        ),
        # local→remote (bastion), bare destination
        "step-2": SyncConfig(
            slug="step-2",
            source=SyncEndpoint(
                volume="stage-local-hl-snapshots",
                hard_link_snapshots=hl_src,
            ),
            destination=SyncEndpoint(
                volume="stage-remote-bare",
            ),
        ),
        # remote→remote same-server (bastion), btrfs destination
        "step-3": SyncConfig(
            slug="step-3",
            source=SyncEndpoint(
                volume="stage-remote-bare",
            ),
            destination=SyncEndpoint(
                volume="stage-remote-btrfs-snapshots",
                btrfs_snapshots=btrfs_dst,
            ),
        ),
        # remote→remote same-server (bastion), bare dest on btrfs
        "step-4": SyncConfig(
            slug="step-4",
            source=SyncEndpoint(
                volume="stage-remote-btrfs-snapshots",
                btrfs_snapshots=btrfs_src,
            ),
            destination=SyncEndpoint(
                volume="stage-remote-btrfs-bare",
            ),
        ),
        # remote→remote same-server (bastion), HL destination
        "step-5": SyncConfig(
            slug="step-5",
            source=SyncEndpoint(
                volume="stage-remote-btrfs-bare",
            ),
            destination=SyncEndpoint(
                volume="stage-remote-hl-snapshots",
                hard_link_snapshots=hl_dst,
            ),
        ),
        # remote (bastion)→local, bare destination
        "step-6": SyncConfig(
            slug="step-6",
            source=SyncEndpoint(
                volume="stage-remote-hl-snapshots",
                hard_link_snapshots=hl_src,
            ),
            destination=SyncEndpoint(volume="dst-local-bare"),
        ),
    }

    return Config(
        ssh_endpoints={
            "bastion": bastion_endpoint,
            "via-bastion": proxied_endpoint,
        },
        volumes=volumes,
        syncs=syncs,
    )


def _assert_trees_equal(expected: Path, actual: Path) -> None:
    """Assert two directory trees have identical structure and content."""
    expected_files = {
        p.relative_to(expected): p
        for p in sorted(expected.rglob("*"))
        if p.is_file() and not p.name.startswith(".nbkp-")
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
        assert (
            actual_files[rel].read_bytes() == exp_path.read_bytes()
        ), f"content mismatch: {rel}"


class TestChainSync:
    def test_data_propagates_through_chain(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        bastion_container: SshEndpoint,
        proxied_ssh_endpoint: SshEndpoint,
    ) -> None:
        """Data seeded in src-local-bare arrives at
        dst-local-bare after traversing the full chain."""
        # 1. Build config
        config = _build_chain_config(
            tmp_path, bastion_container, proxied_ssh_endpoint
        )

        # 2. Create btrfs subvolume for the btrfs-snapshots volume
        ssh_exec(
            docker_ssh_endpoint,
            f"btrfs subvolume create {BTRFS_SNAPSHOTS_PATH}",
        )

        # 3. Create sentinels
        def _run_remote(cmd: str) -> None:
            ssh_exec(docker_ssh_endpoint, cmd)

        create_seed_sentinels(config, remote_exec=_run_remote)

        # 4. Seed data in src-local-bare only
        src_vol = config.volumes["src-local-bare"]
        seed_volume(src_vol)
        src = tmp_path / "src-local-bare"

        # 5. Check all syncs — all should be active
        resolved = resolve_all_endpoints(config)
        _, sync_statuses = check_all_syncs(config, resolved_endpoints=resolved)
        for slug, status in sync_statuses.items():
            assert status.active, (
                f"{slug}: " f"{[r.value for r in status.reasons]}"
            )

        # 6. Run all syncs (topologically ordered)
        results = run_all_syncs(
            config,
            sync_statuses,
            resolved_endpoints=resolved,
        )
        for r in results:
            assert r.success, f"{r.sync_slug}: {r.detail}"

        # 7. Verify final destination matches source
        dst = tmp_path / "dst-local-bare"
        _assert_trees_equal(src, dst)

        # 8. Verify topological ordering
        slugs = [r.sync_slug for r in results]
        for i in range(1, 6):
            assert slugs.index(f"step-{i}") < slugs.index(f"step-{i + 1}")

        # 9. Verify snapshot artifacts on intermediate volumes
        #    HL dest (step-1): latest symlink on local-hl
        local_hl = tmp_path / "stage-local-hl-snapshots"
        assert (local_hl / "latest").is_symlink()
        _assert_trees_equal(src, local_hl / "latest")

        #    Btrfs dest (step-3): snapshot + latest symlink on remote-btrfs
        snap_check = ssh_exec(
            docker_ssh_endpoint,
            f"ls {BTRFS_SNAPSHOTS_PATH}/snapshots/",
        )
        assert snap_check.stdout.strip()
        btrfs_link = ssh_exec(
            docker_ssh_endpoint,
            f"readlink {BTRFS_SNAPSHOTS_PATH}/latest",
        )
        assert "snapshots/" in btrfs_link.stdout

        #    HL dest (step-5): latest symlink on remote-hl
        hl_check = ssh_exec(
            docker_ssh_endpoint,
            f"readlink {REMOTE_BACKUP_PATH}/hl/latest",
        )
        assert "snapshots/" in hl_check.stdout

        # 10. Verify sentinel handling on final destination
        step6 = config.syncs["step-6"]
        assert_sentinels_after_sync(step6, config, docker_ssh_endpoint)
