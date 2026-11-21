"""A backup topology on one fake NAS, for driving whole commands.

Both volumes of :func:`nas_config` live on the remote host ``nas``, so every
probe, snapshot operation and udisks step a command performs on them goes
through the injected command runner; :func:`healthy_nas` answers as a
healthy host does.  Commands run against it with::

    runner.invoke(app, [...], obj=fake_runtime(run=healthy_nas()))
"""

from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    HardLinkSnapshotConfig,
    RemoteVolume,
    SnapshotMode,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)
from tests.fakes import FakeCommandRunner
from tests.preflight.fake_hosts import healthy_host


def nas_config(
    *,
    snapshots: SnapshotMode = "none",
    max_snapshots: int | None = None,
    syncs: tuple[str, ...] = ("photos",),
) -> Config:
    """One sync per name in *syncs*: ``/data/<name>`` → ``/backup/<name>`` on
    ``nas``, the destination taking *snapshots* (keeping *max_snapshots*)."""
    return Config(
        ssh_endpoints={"nas": SshEndpoint(slug="nas", host="nas.example.com")},
        volumes={
            "data": RemoteVolume(slug="data", ssh_endpoint="nas", path="/data"),
            "backup": RemoteVolume(slug="backup", ssh_endpoint="nas", path="/backup"),
        },
        sync_endpoints={
            **{
                f"{name}-src": SyncEndpoint(
                    slug=f"{name}-src", volume="data", subdir=name
                )
                for name in syncs
            },
            **{
                f"{name}-dst": SyncEndpoint(
                    slug=f"{name}-dst",
                    volume="backup",
                    subdir=name,
                    btrfs_snapshots=BtrfsSnapshotConfig(
                        enabled=snapshots == "btrfs", max_snapshots=max_snapshots
                    ),
                    hard_link_snapshots=HardLinkSnapshotConfig(
                        enabled=snapshots == "hard-link", max_snapshots=max_snapshots
                    ),
                )
                for name in syncs
            },
        },
        syncs={
            name: SyncConfig(slug=name, source=f"{name}-src", destination=f"{name}-dst")
            for name in syncs
        },
    )


def healthy_nas(
    run: FakeCommandRunner | None = None, *, fs_type: str = "btrfs"
) -> FakeCommandRunner:
    """*run* answering what its rules leave out as a healthy ``nas`` does:
    every sentinel present, GNU rsync, btrfs with ``user_subvol_rm_allowed``,
    ``latest`` pointing to ``/dev/null`` (see :func:`healthy_host`)."""
    return healthy_host(run, fs_type=fs_type)
