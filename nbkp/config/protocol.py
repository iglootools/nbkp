from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Annotated, Dict, List, Literal, Optional, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)


def _to_kebab(name: str) -> str:
    return name.replace("_", "-")


class _BaseModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_kebab,
        populate_by_name=True,
    )


Slug = Annotated[
    str,
    Field(
        min_length=1,
        max_length=50,
        pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$",
    ),
]


class LocalVolume(_BaseModel):
    model_config = ConfigDict(frozen=True)
    type: Literal["local"] = "local"
    """A local filesystem volume."""
    slug: Slug
    path: str = Field(..., min_length=1)

    @field_validator("path", mode="before")
    @classmethod
    def normalize_path(cls, v: Any) -> str:
        if not isinstance(v, str):
            return v  # type: ignore[no-any-return, return-value]
        return str(Path(v).expanduser())


class SshConnectionOptions(_BaseModel):
    """SSH connection options.

    These fields map to parameters across three layers:
    - SSH client: ssh(1) -o options
    - Paramiko: SSHClient.connect() kwargs
      https://docs.paramiko.org/en/stable/api/client.html
    - Fabric: Connection() constructor
      https://docs.fabfile.org/en/stable/api/connection.html
    """

    model_config = ConfigDict(frozen=True)

    # Connection
    # SSH: ConnectTimeout | Paramiko: timeout | Fabric: connect_timeout
    connect_timeout: int = Field(default=10, ge=1)
    # SSH: Compression | Paramiko: compress
    compress: bool = False
    # SSH: ServerAliveInterval | Paramiko: transport.set_keepalive()
    server_alive_interval: Optional[int] = Field(default=None, ge=1)

    # Authentication
    # Paramiko: allow_agent — use SSH agent for key lookup
    allow_agent: bool = True
    # Paramiko: look_for_keys — search ~/.ssh/ for keys
    look_for_keys: bool = True

    # Timeouts
    # Paramiko: banner_timeout — wait for SSH banner
    banner_timeout: Optional[float] = Field(default=None, ge=0)
    # Paramiko: auth_timeout — wait for auth response
    auth_timeout: Optional[float] = Field(default=None, ge=0)
    # Paramiko: channel_timeout — wait for channel open
    channel_timeout: Optional[float] = Field(default=None, ge=0)

    # Host key verification
    # SSH: StrictHostKeyChecking
    # Paramiko: SSHClient.set_missing_host_key_policy()
    strict_host_key_checking: bool = True
    # SSH: UserKnownHostsFile
    # Paramiko: SSHClient.load_host_keys()
    known_hosts_file: Optional[str] = None

    # Forwarding
    # SSH: ForwardAgent | Fabric: forward_agent
    forward_agent: bool = False

    # Algorithm restrictions
    # Paramiko: disabled_algorithms — disable specific algorithms
    # (Paramiko/Fabric only — no SSH CLI equivalent)
    disabled_algorithms: Optional[Dict[str, List[str]]] = None


class SshEndpoint(_BaseModel):
    model_config = ConfigDict(frozen=True)
    slug: Slug
    host: str = Field(..., min_length=1)
    port: int = Field(default=22, ge=1, le=65535)
    user: Optional[str] = None
    key: Optional[str] = None

    @field_validator("key", mode="before")
    @classmethod
    def normalize_key(cls, v: Any) -> str | None:
        if not isinstance(v, str):
            return None
        return str(Path(v).expanduser())

    connection_options: SshConnectionOptions = Field(
        default_factory=lambda: SshConnectionOptions()
    )
    proxy_jump: Optional[str] = None
    proxy_jumps: Optional[List[str]] = None
    location: Optional[str] = None
    locations: Optional[List[str]] = None
    extends: Optional[str] = None

    @model_validator(mode="after")
    def validate_proxy_exclusivity(self) -> SshEndpoint:
        if self.proxy_jump is not None and self.proxy_jumps is not None:
            raise ValueError("proxy-jump and proxy-jumps are mutually exclusive")
        return self

    @model_validator(mode="after")
    def validate_location_exclusivity(self) -> SshEndpoint:
        if self.location is not None and self.locations is not None:
            raise ValueError("location and locations are mutually exclusive")
        return self

    @property
    def proxy_jump_chain(self) -> list[str]:
        """Return the proxy-jump chain as a list of slugs."""
        if self.proxy_jumps is not None:
            return list(self.proxy_jumps)
        elif self.proxy_jump is not None:
            return [self.proxy_jump]
        else:
            return []

    @property
    def location_list(self) -> list[str]:
        """Return locations as a list."""
        if self.locations is not None:
            return list(self.locations)
        elif self.location is not None:
            return [self.location]
        else:
            return []


class RemoteVolume(_BaseModel):
    model_config = ConfigDict(frozen=True)
    type: Literal["remote"] = "remote"
    """A remote volume accessible via SSH."""
    slug: Slug
    ssh_endpoint: str = Field(..., min_length=1)
    ssh_endpoints: Optional[List[str]] = None
    path: str = Field(..., min_length=1)

    @field_validator("path", mode="before")
    @classmethod
    def normalize_path(cls, v: Any) -> str:
        if not isinstance(v, str):
            return v  # type: ignore[no-any-return, return-value]
        stripped = v.rstrip("/")
        return stripped if stripped else "/"


Volume = Annotated[Union[LocalVolume, RemoteVolume], Field(discriminator="type")]


class BtrfsSnapshotConfig(_BaseModel):
    """Configuration for btrfs snapshot management."""

    model_config = ConfigDict(frozen=True)
    enabled: bool = False
    max_snapshots: Optional[int] = Field(default=None, ge=1)


class HardLinkSnapshotConfig(_BaseModel):
    """Configuration for hard-link-based snapshot management."""

    model_config = ConfigDict(frozen=True)
    enabled: bool = False
    max_snapshots: Optional[int] = Field(default=None, ge=1)


class SyncEndpoint(_BaseModel):
    """A reusable sync endpoint: volume + optional subdir + snapshots.

    Defined at the top level of the config under ``sync-endpoints``
    and referenced by slug from ``syncs``.  When used as a
    destination, snapshot config controls how backups are stored.
    When used as a source, snapshot config tells rsync to read
    from the ``latest/`` directory instead of the volume root.
    """

    slug: Slug
    volume: str = Field(..., min_length=1)
    subdir: Optional[str] = None

    @field_validator("subdir", mode="before")
    @classmethod
    def normalize_subdir(cls, v: Any) -> str | None:
        if not isinstance(v, str):
            return None
        stripped = v.strip("/")
        return stripped if stripped else None

    btrfs_snapshots: BtrfsSnapshotConfig = Field(
        default_factory=lambda: BtrfsSnapshotConfig()
    )
    hard_link_snapshots: HardLinkSnapshotConfig = Field(
        default_factory=lambda: HardLinkSnapshotConfig()
    )

    @model_validator(mode="after")
    def validate_snapshot_exclusivity(
        self,
    ) -> SyncEndpoint:
        if self.btrfs_snapshots.enabled and self.hard_link_snapshots.enabled:
            raise ValueError(
                "btrfs-snapshots and hard-link-snapshots are mutually exclusive"
            )
        return self

    @property
    def snapshot_mode(
        self,
    ) -> Literal["none", "btrfs", "hard-link"]:
        if self.btrfs_snapshots.enabled:
            return "btrfs"
        elif self.hard_link_snapshots.enabled:
            return "hard-link"
        else:
            return "none"


class RsyncOptions(_BaseModel):
    """Rsync flag configuration for a sync operation."""

    model_config = ConfigDict(frozen=True)
    compress: bool = False
    checksum: bool = True
    default_options_override: Optional[List[str]] = None
    extra_options: List[str] = Field(default_factory=list)


class SyncConfig(_BaseModel):
    """Configuration for a single sync operation."""

    slug: Slug
    source: str = Field(..., min_length=1)
    destination: str = Field(..., min_length=1)
    enabled: bool = True
    rsync_options: RsyncOptions = Field(default_factory=lambda: RsyncOptions())
    filters: List[str] = Field(default_factory=list)
    filter_file: Optional[str] = None

    @field_validator("filter_file", mode="before")
    @classmethod
    def normalize_filter_file(cls, v: Any) -> str | None:
        if not isinstance(v, str):
            return None
        return str(Path(v).expanduser())

    @field_validator("filters", mode="before")
    @classmethod
    def normalize_filters(cls, v: Any) -> list[str]:
        result: list[str] = []
        for item in v:
            match item:
                case str():
                    result.append(item)
                case {"include": str() as pattern}:
                    result.append(f"+ {pattern}")
                case {"exclude": str() as pattern}:
                    result.append(f"- {pattern}")
                case _:
                    raise ValueError(
                        f"Filter must be a string or a dict"
                        f" with 'include'/'exclude' key,"
                        f" got: {item!r}"
                    )
        return result


class NetworkType(str, Enum):
    """Network type for endpoint filtering."""

    PRIVATE = "private"
    PUBLIC = "public"


class EndpointFilter(_BaseModel):
    """Endpoint selection filter (not serialized)."""

    model_config = ConfigDict(frozen=True)
    locations: List[str] = Field(default_factory=list)
    exclude_locations: List[str] = Field(default_factory=list)
    network: Optional[NetworkType] = None


class Config(_BaseModel):
    """Top-level NBKP configuration."""

    ssh_endpoints: Dict[str, SshEndpoint] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def resolve_ssh_endpoint_extends(cls, data: Any) -> Any:
        """Resolve `extends` inheritance on ssh-endpoints."""
        if not isinstance(data, dict):
            return data
        endpoints = data.get("ssh-endpoints") or data.get("ssh_endpoints") or {}
        if not isinstance(endpoints, dict):
            return data

        resolved: dict[str, Any] = {}

        def _resolve(slug: str, chain: list[str]) -> Any:
            if slug in resolved:
                return resolved[slug]
            ep = endpoints[slug]
            if not isinstance(ep, dict):
                resolved[slug] = ep
                return ep
            parent_slug = ep.get("extends")
            if parent_slug is None:
                resolved[slug] = ep
                return ep
            if parent_slug in chain:
                chain_str = " -> ".join(chain + [parent_slug])
                raise ValueError(f"Circular extends chain: {chain_str}")
            if parent_slug not in endpoints:
                raise ValueError(
                    f"Endpoint '{slug}' extends unknown endpoint '{parent_slug}'"
                )
            parent = _resolve(parent_slug, chain + [slug])
            if not isinstance(parent, dict):
                resolved[slug] = ep
                return ep
            merged = {
                **parent,
                **{k: v for k, v in ep.items() if k != "extends"},
            }
            # If child sets proxy-jump or proxy-jumps, remove
            # the other to avoid exclusivity clash with parent
            proxy_keys = {"proxy-jump", "proxy-jumps"}
            child_proxy_keys = proxy_keys & set(ep.keys())
            if child_proxy_keys:
                for k in proxy_keys - child_proxy_keys:
                    merged.pop(k, None)
            # Same for location / locations
            loc_keys = {"location", "locations"}
            child_loc_keys = loc_keys & set(ep.keys())
            if child_loc_keys:
                for k in loc_keys - child_loc_keys:
                    merged.pop(k, None)
            resolved[slug] = merged
            return merged

        for slug in endpoints:
            _resolve(slug, [])

        data = {**data}
        if "ssh-endpoints" in data:
            data["ssh-endpoints"] = resolved
        else:
            data["ssh_endpoints"] = resolved
        return data

    @field_validator("ssh_endpoints", mode="before")
    @classmethod
    def inject_ssh_endpoint_slugs(cls, v: Any, info: ValidationInfo) -> Any:
        return {
            slug: (
                {**data, "slug": slug}
                if isinstance(data, dict) and "slug" not in data
                else data
            )
            for slug, data in v.items()
        }

    volumes: Dict[str, Volume] = Field(default_factory=dict)

    @field_validator("volumes", mode="before")
    @classmethod
    def inject_volume_slugs(cls, v: Any, info: ValidationInfo) -> Any:
        return {
            slug: (
                {**data, "slug": slug}
                if isinstance(data, dict) and "slug" not in data
                else data
            )
            for slug, data in v.items()
        }

    sync_endpoints: Dict[str, SyncEndpoint] = Field(default_factory=dict)

    @field_validator("sync_endpoints", mode="before")
    @classmethod
    def inject_sync_endpoint_slugs(cls, v: Any, info: ValidationInfo) -> Any:
        return {
            slug: (
                {**data, "slug": slug}
                if isinstance(data, dict) and "slug" not in data
                else data
            )
            for slug, data in v.items()
        }

    syncs: Dict[str, SyncConfig] = Field(default_factory=dict)

    @field_validator("syncs", mode="before")
    @classmethod
    def inject_sync_slugs(cls, v: Any, info: ValidationInfo) -> Any:
        return {
            slug: (
                {**data, "slug": slug}
                if isinstance(data, dict) and "slug" not in data
                else data
            )
            for slug, data in v.items()
        }

    def source_endpoint(self, sync: SyncConfig) -> SyncEndpoint:
        """Resolve the source sync endpoint for a sync."""
        return self.sync_endpoints[sync.source]

    def destination_endpoint(self, sync: SyncConfig) -> SyncEndpoint:
        """Resolve the destination sync endpoint for a sync."""
        return self.sync_endpoints[sync.destination]

    def resolve_endpoint_for_volume(
        self,
        vol: RemoteVolume,
        endpoint_filter: EndpointFilter | None = None,
    ) -> SshEndpoint:
        """Select the best SSH endpoint for a remote volume.

        Uses ``endpoint_filter`` (location, network) to narrow
        candidates.  Falls back to the primary ``ssh_endpoint``.
        """
        from ..remote.resolution import is_private_host

        candidates = (
            list(vol.ssh_endpoints) if vol.ssh_endpoints else [vol.ssh_endpoint]
        )

        ef = endpoint_filter
        if ef is None:
            return self.ssh_endpoints[candidates[0]]

        # DNS reachability: drop endpoints whose host
        # cannot be resolved
        reachable = [
            slug
            for slug in candidates
            if is_private_host(self.ssh_endpoints[slug].host) is not None
        ]
        if not reachable:
            return self.ssh_endpoints[vol.ssh_endpoint]

        # Exclude locations
        if ef.exclude_locations:
            excl = set(ef.exclude_locations)
            filtered = [
                slug
                for slug in reachable
                if not (excl & set(self.ssh_endpoints[slug].location_list))
            ]
            if filtered:
                reachable = filtered

        # Include locations
        if ef.locations:
            filter_locs = set(ef.locations)
            by_loc = [
                slug
                for slug in reachable
                if filter_locs & set(self.ssh_endpoints[slug].location_list)
            ]
            if by_loc:
                reachable = by_loc

        # Network filter (private / public)
        if ef.network is not None:
            want_private = ef.network == NetworkType.PRIVATE
            by_net = [
                slug
                for slug in reachable
                if is_private_host(self.ssh_endpoints[slug].host) == want_private
            ]
            if by_net:
                reachable = by_net

        # Deterministic pick: first candidate in original order
        return self.ssh_endpoints[reachable[0]]

    def resolve_proxy_chain(self, server: SshEndpoint) -> list[SshEndpoint]:
        """Resolve the proxy-jump chain as a list of SshEndpoints."""
        return [self.ssh_endpoints[slug] for slug in server.proxy_jump_chain]

    @model_validator(mode="after")
    def validate_cross_references(self) -> Config:
        for slug, server in self.ssh_endpoints.items():
            chain = server.proxy_jump_chain
            for hop in chain:
                if hop not in self.ssh_endpoints:
                    raise ValueError(
                        f"Server '{slug}' references unknown proxy-jump server '{hop}'"
                    )
            # Circular detection via BFS through transitive
            # proxy chains
            visited: set[str] = {slug}
            queue = list(chain)
            while queue:
                current = queue.pop(0)
                if current in visited:
                    raise ValueError(
                        f"Circular proxy-jump chain "
                        f"detected starting from "
                        f"server '{slug}'"
                    )
                visited.add(current)
                queue.extend(self.ssh_endpoints[current].proxy_jump_chain)

        for vol_slug, vol in self.volumes.items():
            match vol:
                case RemoteVolume():
                    if vol.ssh_endpoint not in self.ssh_endpoints:
                        ref = vol.ssh_endpoint
                        raise ValueError(
                            f"Volume '{vol_slug}' references "
                            f"unknown ssh-endpoint '{ref}'"
                        )
                    if vol.ssh_endpoints is not None:
                        for ep_ref in vol.ssh_endpoints:
                            if ep_ref not in self.ssh_endpoints:
                                raise ValueError(
                                    f"Volume '{vol_slug}'"
                                    f" references unknown"
                                    f" ssh-endpoint"
                                    f" '{ep_ref}'"
                                )
        # Sync endpoint volume references
        for ep_slug, ep in self.sync_endpoints.items():
            if ep.volume not in self.volumes:
                raise ValueError(
                    f"Sync endpoint '{ep_slug}' references unknown volume '{ep.volume}'"
                )

        # Unique (volume, subdir) per sync endpoint
        seen_locations: dict[tuple[str, str | None], str] = {}
        for ep_slug, ep in self.sync_endpoints.items():
            loc = (ep.volume, ep.subdir)
            if loc in seen_locations:
                other = seen_locations[loc]
                subdir_msg = f" subdir '{ep.subdir}'" if ep.subdir else ""
                raise ValueError(
                    f"Sync endpoints '{other}' and"
                    f" '{ep_slug}' both target volume"
                    f" '{ep.volume}'{subdir_msg}"
                )
            seen_locations[loc] = ep_slug

        # Sync source/destination endpoint references
        for sync_slug, sync in self.syncs.items():
            if sync.source not in self.sync_endpoints:
                raise ValueError(
                    f"Sync '{sync_slug}' references"
                    f" unknown source endpoint"
                    f" '{sync.source}'"
                )
            if sync.destination not in self.sync_endpoints:
                raise ValueError(
                    f"Sync '{sync_slug}' references"
                    f" unknown destination endpoint"
                    f" '{sync.destination}'"
                )

        # Unique destination per sync
        dest_owners: dict[str, str] = {}
        for sync_slug, sync in self.syncs.items():
            if sync.destination in dest_owners:
                other = dest_owners[sync.destination]
                raise ValueError(
                    f"Syncs '{other}' and"
                    f" '{sync_slug}' share"
                    f" destination endpoint"
                    f" '{sync.destination}'"
                )
            dest_owners[sync.destination] = sync_slug

        # Cross-server remote-to-remote check
        for sync_slug, sync in self.syncs.items():
            src_ep = self.sync_endpoints[sync.source]
            dst_ep = self.sync_endpoints[sync.destination]
            src_vol = self.volumes[src_ep.volume]
            dst_vol = self.volumes[dst_ep.volume]
            if (
                isinstance(src_vol, RemoteVolume)
                and isinstance(dst_vol, RemoteVolume)
                and src_vol.ssh_endpoint != dst_vol.ssh_endpoint
            ):
                raise ValueError(
                    f"Sync '{sync_slug}' has source on"
                    f" '{src_vol.ssh_endpoint}' and"
                    f" destination on"
                    f" '{dst_vol.ssh_endpoint}'."
                    f" Cross-server remote-to-remote"
                    f" syncs are not supported."
                    f" Use two separate syncs through"
                    f" the local machine instead."
                )
        return self
