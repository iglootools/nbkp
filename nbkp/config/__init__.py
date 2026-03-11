"""Configuration types and loading."""

from .loader import ConfigError, ConfigErrorReason, find_config_file, load_config
from .protocol import (
    BtrfsSnapshotConfig,
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    RemoteVolume,
    RsyncOptions,
    SnapshotMode,
    SshEndpoint,
    Slug,
    SshConnectionOptions,
    SyncConfig,
    SyncEndpoint,
    Volume,
)
from nbkp.remote.resolution import (
    EndpointFilter,
    NetworkType,
    ResolvedEndpoint,
    ResolvedEndpoints,
    resolve_all_endpoints,
    resolve_endpoint_for_volume,
    resolve_proxy_chain,
)

__all__ = [
    "BtrfsSnapshotConfig",
    "Config",
    "ConfigError",
    "ConfigErrorReason",
    "EndpointFilter",
    "HardLinkSnapshotConfig",
    "LocalVolume",
    "NetworkType",
    "RemoteVolume",
    "RsyncOptions",
    "ResolvedEndpoint",
    "ResolvedEndpoints",
    "SshEndpoint",
    "Slug",
    "SnapshotMode",
    "SshConnectionOptions",
    "SyncConfig",
    "SyncEndpoint",
    "Volume",
    "find_config_file",
    "load_config",
    "resolve_all_endpoints",
    "resolve_endpoint_for_volume",
    "resolve_proxy_chain",
]
