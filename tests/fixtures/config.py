"""Configs the tests share: one sync between two volumes."""

from collections.abc import Sequence
from typing import TypedDict, Unpack

from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    RsyncOptions,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
    Volume,
)


class SyncFields(TypedDict, total=False):
    """The fields of the sync :func:`two_volume_config` builds that a test may set."""

    enabled: bool
    rsync_options: RsyncOptions
    filters: list[str]
    filter_file: str | None


def two_volume_config(
    *,
    slug: str = "s1",
    src: Volume | None = None,
    dst: Volume | None = None,
    ssh_endpoints: Sequence[SshEndpoint] = (),
    src_subdir: str | None = None,
    dst_subdir: str | None = None,
    src_btrfs: BtrfsSnapshotConfig | None = None,
    src_hard_link: HardLinkSnapshotConfig | None = None,
    dst_btrfs: BtrfsSnapshotConfig | None = None,
    dst_hard_link: HardLinkSnapshotConfig | None = None,
    **sync_fields: Unpack[SyncFields],
) -> tuple[SyncConfig, Config]:
    """
    Return sync *slug* from endpoint ``ep-src`` on volume *src* to ``ep-dst`` on *dst*.

    *src* and *dst* default to local volumes ``/mnt/src`` and ``/mnt/dst``; they
    must be named ``src`` and ``dst``. *ssh_endpoints* are the SSH endpoints the
    remote volumes and their proxies name.
    """
    sync = SyncConfig(slug=slug, source="ep-src", destination="ep-dst", **sync_fields)
    config = Config(
        ssh_endpoints={ep.slug: ep for ep in ssh_endpoints},
        volumes={
            "src": src or LocalVolume(slug="src", path="/mnt/src"),
            "dst": dst or LocalVolume(slug="dst", path="/mnt/dst"),
        },
        sync_endpoints={
            "ep-src": SyncEndpoint(
                slug="ep-src",
                volume="src",
                subdir=src_subdir,
                btrfs_snapshots=src_btrfs or BtrfsSnapshotConfig(),
                hard_link_snapshots=src_hard_link or HardLinkSnapshotConfig(),
            ),
            "ep-dst": SyncEndpoint(
                slug="ep-dst",
                volume="dst",
                subdir=dst_subdir,
                btrfs_snapshots=dst_btrfs or BtrfsSnapshotConfig(),
                hard_link_snapshots=dst_hard_link or HardLinkSnapshotConfig(),
            ),
        },
        syncs={slug: sync},
    )
    return sync, config
