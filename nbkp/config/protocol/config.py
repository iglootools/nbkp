"""Top-level NBKP configuration model."""

from __future__ import annotations

from collections import deque
from typing import Any, Dict

from pydantic import Field, ValidationInfo, field_validator, model_validator

from .base import _BaseModel
from .ssh_endpoint import SshEndpoint
from .sync import SyncConfig
from .sync_endpoint import SyncEndpoint
from .volume import RemoteVolume, Volume


def _remove_stale_exclusive_keys(
    merged: dict[str, Any], child: dict[str, Any], key_group: set[str]
) -> None:
    """Remove parent-only keys from an exclusive key group when child overrides one."""
    child_keys = key_group & set(child.keys())
    if child_keys:
        for k in key_group - child_keys:
            merged.pop(k, None)


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
            # If child sets one of an exclusive key pair, remove
            # the other to avoid exclusivity clash with parent
            _remove_stale_exclusive_keys(merged, ep, {"proxy-jump", "proxy-jumps"})
            _remove_stale_exclusive_keys(merged, ep, {"location", "locations"})
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

    def orphan_ssh_endpoints(self) -> list[str]:
        """SSH endpoints not referenced by any volume or proxy-jump chain."""
        used = {
            ref
            for vol in self.volumes.values()
            if isinstance(vol, RemoteVolume)
            for ref in [vol.ssh_endpoint, *(vol.ssh_endpoints or [])]
        } | {hop for ep in self.ssh_endpoints.values() for hop in ep.proxy_jump_chain}
        return sorted(set(self.ssh_endpoints) - used)

    def orphan_volumes(self) -> list[str]:
        """Volumes not referenced by any sync endpoint."""
        used = {ep.volume for ep in self.sync_endpoints.values()}
        return sorted(set(self.volumes) - used)

    def orphan_sync_endpoints(self) -> list[str]:
        """Sync endpoints not referenced by any sync."""
        used = {
            ref
            for sync in self.syncs.values()
            for ref in [sync.source, sync.destination]
        }
        return sorted(set(self.sync_endpoints) - used)

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
            queue: deque[str] = deque(chain)
            while queue:
                current = queue.popleft()
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
