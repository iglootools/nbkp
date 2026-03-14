"""Fake config builders for manual testing."""

from __future__ import annotations

from . import (
    BtrfsSnapshotConfig,
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)


def bastion_server() -> SshEndpoint:
    return SshEndpoint(
        slug="bastion",
        host="bastion.example.com",
        user="admin",
    )


def bastion2_server() -> SshEndpoint:
    return SshEndpoint(
        slug="bastion2",
        host="bastion2.internal",
        user="admin",
    )


def nas_server() -> SshEndpoint:
    return SshEndpoint(
        slug="nas",
        host="nas.example.com",
        port=5022,
        user="backup",
        key="~/.ssh/nas_ed25519",
        proxy_jumps=["bastion", "bastion2"],
        locations=["home", "travel"],
    )


def nas_public_server() -> SshEndpoint:
    return SshEndpoint(
        slug="nas-public",
        host="nas.public.example.com",
        port=5022,
        user="backup",
        key="~/.ssh/nas_ed25519",
        location="travel",
    )


def base_volumes() -> dict[str, LocalVolume | RemoteVolume]:
    return {
        "laptop": LocalVolume(slug="laptop", path="/mnt/data"),
        "usb-drive": LocalVolume(slug="usb-drive", path="/mnt/usb-backup"),
        "nas-backup": RemoteVolume(
            slug="nas-backup",
            ssh_endpoint="nas",
            ssh_endpoints=["nas", "nas-public"],
            path="/volume1/backups",
        ),
    }


def base_ssh_endpoints() -> dict[str, SshEndpoint]:
    return {
        "bastion": bastion_server(),
        "bastion2": bastion2_server(),
        "nas": nas_server(),
        "nas-public": nas_public_server(),
    }


def base_sync_endpoints() -> dict[str, SyncEndpoint]:
    return {
        "laptop-photos": SyncEndpoint(
            slug="laptop-photos",
            volume="laptop",
            subdir="photos",
        ),
        "usb-photos": SyncEndpoint(
            slug="usb-photos",
            volume="usb-drive",
            subdir="photos",
            btrfs_snapshots=BtrfsSnapshotConfig(enabled=True, max_snapshots=10),
        ),
        "laptop-docs": SyncEndpoint(
            slug="laptop-docs",
            volume="laptop",
            subdir="documents",
        ),
        "nas-docs": SyncEndpoint(
            slug="nas-docs",
            volume="nas-backup",
            subdir="docs",
            hard_link_snapshots=HardLinkSnapshotConfig(enabled=True, max_snapshots=10),
        ),
        "laptop-music": SyncEndpoint(
            slug="laptop-music",
            volume="laptop",
            subdir="music",
        ),
        "usb-music": SyncEndpoint(
            slug="usb-music",
            volume="usb-drive",
            subdir="music",
            hard_link_snapshots=HardLinkSnapshotConfig(enabled=True, max_snapshots=5),
        ),
        "laptop-root": SyncEndpoint(
            slug="laptop-root",
            volume="laptop",
        ),
    }


def base_syncs() -> dict[str, SyncConfig]:
    return {
        "photos-to-usb": SyncConfig(
            slug="photos-to-usb",
            source="laptop-photos",
            destination="usb-photos",
            filters=["+ *.jpg", "- *.tmp"],
        ),
        "docs-to-nas": SyncConfig(
            slug="docs-to-nas",
            source="laptop-docs",
            destination="nas-docs",
        ),
        "music-to-usb": SyncConfig(
            slug="music-to-usb",
            source="laptop-music",
            destination="usb-music",
        ),
    }


def config_show_config() -> Config:
    """Config exercising all display paths for config show."""
    return Config(
        ssh_endpoints=base_ssh_endpoints(),
        volumes=base_volumes(),
        sync_endpoints=base_sync_endpoints(),
        syncs=base_syncs(),
    )
