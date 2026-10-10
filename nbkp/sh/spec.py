"""Per-sync facts shared by the preflight and step builders."""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Config, LocalVolume, RemoteVolume, SyncConfig, SyncEndpoint
from ..fsprotocol import LATEST_LINK, SNAPSHOTS_DIR, STAGING_DIR
from ..remote.endpoints import ResolvedEndpoints


@dataclass(frozen=True)
class SyncSpec:
    """Everything needed to emit shell code for one sync.

    Paths are the effective paths baked into the script (possibly
    relative to ``NBKP_SCRIPT_DIR`` placeholders for local volumes).
    """

    slug: str
    sync: SyncConfig
    config: Config
    src_ep: SyncEndpoint
    dst_ep: SyncEndpoint
    src_vol: LocalVolume | RemoteVolume
    dst_vol: LocalVolume | RemoteVolume
    src_vol_path: str
    dst_vol_path: str
    src_path: str
    dst_path: str
    has_upstream: bool
    platform: str
    resolved_endpoints: ResolvedEndpoints

    @property
    def snapshots_dir(self) -> str:
        return f"{self.dst_path}/{SNAPSHOTS_DIR}"

    @property
    def staging_dir(self) -> str:
        return f"{self.dst_path}/{STAGING_DIR}"

    @property
    def dst_latest(self) -> str:
        return f"{self.dst_path}/{LATEST_LINK}"

    @property
    def max_snapshots(self) -> int | None:
        match self.dst_ep.snapshot_mode:
            case "btrfs":
                return self.dst_ep.btrfs_snapshots.max_snapshots
            case "hard-link":
                return self.dst_ep.hard_link_snapshots.max_snapshots
            case _:
                return None


def build_sync_spec(
    slug: str,
    config: Config,
    vol_paths: dict[str, str],
    resolved_endpoints: ResolvedEndpoints,
    platform: str,
) -> SyncSpec:
    sync = config.syncs[slug]
    src_ep = config.source_endpoint(sync)
    dst_ep = config.destination_endpoint(sync)
    return SyncSpec(
        slug=slug,
        sync=sync,
        config=config,
        src_ep=src_ep,
        dst_ep=dst_ep,
        src_vol=config.volumes[src_ep.volume],
        dst_vol=config.volumes[dst_ep.volume],
        src_vol_path=vol_paths[src_ep.volume],
        dst_vol_path=vol_paths[dst_ep.volume],
        src_path=_join(vol_paths[src_ep.volume], src_ep.subdir),
        dst_path=_join(vol_paths[dst_ep.volume], dst_ep.subdir),
        has_upstream=any(
            s.enabled and s.destination == sync.source for s in config.syncs.values()
        ),
        platform=platform,
        resolved_endpoints=resolved_endpoints,
    )


def _join(base: str, subdir: str | None) -> str:
    return f"{base}/{subdir}" if subdir else base
