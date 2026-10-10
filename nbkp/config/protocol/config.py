"""Top-level NBKP configuration model."""

from __future__ import annotations

import enum
from collections.abc import Iterable, Iterator, Mapping
from itertools import combinations
from typing import Any

from pydantic import Field, field_validator, model_validator

from .base import _BaseModel
from .errors import ConfigValidationCode, config_error
from .ssh_endpoint import SshEndpoint
from .sync import SyncConfig
from .sync_endpoint import SyncEndpoint
from .volume import RemoteVolume, Volume


class CredentialProvider(str, enum.Enum):
    """How LUKS passphrases are retrieved."""

    KEYRING = "keyring"
    PROMPT = "prompt"
    ENV = "env"
    COMMAND = "command"


# Key pairs where setting one half in a child endpoint must drop the other
# half inherited from its parent, so the merge does not trip the exclusivity
# validators.
_EXCLUSIVE_KEY_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"proxy-jump", "proxy-jumps"}),
    frozenset({"location", "locations"}),
)


def _stale_exclusive_keys(child: Mapping[str, Any]) -> set[str]:
    """Inherited keys to drop because the child set the other half of a group."""
    return {
        key
        for group in _EXCLUSIVE_KEY_GROUPS
        if group & child.keys()
        for key in group - child.keys()
    }


def _merge_endpoint(
    parent: Mapping[str, Any], child: Mapping[str, Any]
) -> dict[str, Any]:
    """Overlay a child endpoint's raw fields on its resolved parent's."""
    stale = _stale_exclusive_keys(child)
    merged = {**parent, **{k: v for k, v in child.items() if k != "extends"}}
    return {k: v for k, v in merged.items() if k not in stale}


def _resolve_endpoint(
    endpoints: Mapping[str, Any], slug: str, chain: tuple[str, ...]
) -> Any:
    """Resolve one raw endpoint's ``extends`` chain into a flat mapping.

    ``chain`` holds the slugs visited so far, ending with ``slug``, for cycle
    detection. Non-mapping entries are returned as-is for pydantic to reject.
    """
    ep = endpoints[slug]
    parent_slug = ep.get("extends") if isinstance(ep, dict) else None
    if parent_slug is None:
        return ep
    elif parent_slug in chain:
        raise config_error(
            ConfigValidationCode.CIRCULAR_EXTENDS,
            f"Circular extends chain: {' -> '.join([*chain, parent_slug])}",
        )
    elif parent_slug not in endpoints:
        raise config_error(
            ConfigValidationCode.UNKNOWN_REFERENCE,
            f"Endpoint '{slug}' extends unknown endpoint '{parent_slug}'",
        )
    else:
        parent = _resolve_endpoint(endpoints, parent_slug, (*chain, parent_slug))
        return _merge_endpoint(parent, ep) if isinstance(parent, dict) else ep


def _inject_slugs(entries: Any) -> Any:
    """Copy each mapping key into its entry's ``slug`` field, unless set."""
    match entries:
        case dict():
            return {
                slug: (
                    {**data, "slug": slug}
                    if isinstance(data, dict) and "slug" not in data
                    else data
                )
                for slug, data in entries.items()
            }
        case _:
            return entries


def _unknown_reference(message: str) -> ValueError:
    return config_error(ConfigValidationCode.UNKNOWN_REFERENCE, message)


def _raise_first(errors: Iterable[ValueError]) -> None:
    """Raise the first error a lazy check yields, if any.

    Checks are generators consumed in order, so a later check (e.g. one that
    indexes ``volumes`` by a sync endpoint's volume) only runs once the
    earlier reference checks it relies on have passed.
    """
    error = next(iter(errors), None)
    if error is not None:
        raise error


def _is_nested(path_a: str, path_b: str) -> bool:
    """Whether one subdir (``""`` = volume root) contains the other."""
    norm_a = f"{path_a}/" if path_a else ""
    norm_b = f"{path_b}/" if path_b else ""
    return norm_a.startswith(norm_b) or norm_b.startswith(norm_a)


def _location_error(
    slug_a: str, ep_a: SyncEndpoint, slug_b: str, ep_b: SyncEndpoint
) -> ValueError | None:
    """Error for two sync endpoints on one volume sharing or nesting a path."""
    path_a, path_b = ep_a.subdir or "", ep_b.subdir or ""
    if path_a == path_b:
        subdir_msg = f" subdir '{ep_b.subdir}'" if ep_b.subdir else ""
        return config_error(
            ConfigValidationCode.DUPLICATE_ENDPOINT_LOCATION,
            f"Sync endpoints '{slug_a}' and '{slug_b}' both target volume"
            f" '{ep_b.volume}'{subdir_msg}",
        )
    elif _is_nested(path_a, path_b):
        parent, child = (
            (slug_a, slug_b) if len(path_a) < len(path_b) else (slug_b, slug_a)
        )
        return config_error(
            ConfigValidationCode.NESTED_ENDPOINT,
            f"Sync endpoint '{child}' is nested inside '{parent}' on volume"
            f" '{ep_a.volume}': overlapping endpoint paths are not supported"
            " because sync dependencies cannot be detected between them",
        )
    else:
        return None


class Config(_BaseModel):
    """Top-level NBKP configuration."""

    credential_provider: CredentialProvider = Field(
        default=CredentialProvider.KEYRING,
        description=(
            "How LUKS passphrases are retrieved."
            " Options: keyring, prompt, env, command."
        ),
    )
    credential_command: list[str] | None = Field(
        default=None,
        description=(
            "Command template for the ``command`` credential provider."
            " ``{id}`` is replaced with the passphrase-id."
            ' Example: ``["pass", "show", "nbkp/{id}"]``.'
        ),
    )

    ssh_endpoints: dict[str, SshEndpoint] = Field(default_factory=dict)
    volumes: dict[str, Volume] = Field(default_factory=dict)
    sync_endpoints: dict[str, SyncEndpoint] = Field(default_factory=dict)
    syncs: dict[str, SyncConfig] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def resolve_ssh_endpoint_extends(cls, data: Any) -> Any:
        """Resolve `extends` inheritance on ssh-endpoints."""
        if not isinstance(data, dict):
            return data
        key = "ssh-endpoints" if "ssh-endpoints" in data else "ssh_endpoints"
        endpoints = data.get(key) or {}
        if not isinstance(endpoints, dict):
            return data
        resolved = {
            slug: _resolve_endpoint(endpoints, slug, (slug,)) for slug in endpoints
        }
        return {**data, key: resolved}

    @field_validator(
        "ssh_endpoints", "volumes", "sync_endpoints", "syncs", mode="before"
    )
    @classmethod
    def inject_slugs(cls, v: Any) -> Any:
        """Let entries omit ``slug``: it is the key they are declared under."""
        return _inject_slugs(v)

    def source_endpoint(self, sync: SyncConfig) -> SyncEndpoint:
        """Resolve the source sync endpoint for a sync."""
        return self.sync_endpoints[sync.source]

    def destination_endpoint(self, sync: SyncConfig) -> SyncEndpoint:
        """Resolve the destination sync endpoint for a sync."""
        return self.sync_endpoints[sync.destination]

    def known_locations(self) -> list[str]:
        """All distinct location tags declared across SSH endpoints."""
        return sorted(
            {loc for ep in self.ssh_endpoints.values() for loc in ep.location_list}
        )

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
        _raise_first(
            error
            for check in (
                self._unknown_proxy_jump_errors,
                self._circular_proxy_jump_errors,
                self._volume_reference_errors,
                self._sync_endpoint_location_errors,
                self._sync_reference_errors,
                self._shared_destination_errors,
                self._cross_server_errors,
                self._credential_errors,
            )
            for error in check()
        )
        return self

    def _unknown_proxy_jump_errors(self) -> Iterator[ValueError]:
        """Proxy-jump hops naming an undeclared ssh-endpoint."""
        return (
            _unknown_reference(
                f"Server '{slug}' references unknown proxy-jump server '{hop}'"
            )
            for slug, server in self.ssh_endpoints.items()
            for hop in server.proxy_jump_chain
            if hop not in self.ssh_endpoints
        )

    def _circular_proxy_jump_errors(self) -> Iterator[ValueError]:
        """Servers whose transitive proxy-jump chain leads back to a server."""
        return (
            config_error(
                ConfigValidationCode.CIRCULAR_PROXY_JUMP,
                f"Circular proxy-jump chain detected starting from server '{slug}'",
            )
            for slug in self.ssh_endpoints
            if self._has_proxy_cycle(slug, frozenset())
        )

    def _has_proxy_cycle(self, slug: str, visited: frozenset[str]) -> bool:
        """Whether following proxy-jumps from ``slug`` revisits a server."""
        return slug in visited or any(
            self._has_proxy_cycle(hop, visited | {slug})
            for hop in self.ssh_endpoints[slug].proxy_jump_chain
        )

    def _volume_reference_errors(self) -> Iterator[ValueError]:
        """Volumes naming unknown ssh-endpoints, sync endpoints unknown volumes."""
        yield from (
            _unknown_reference(
                f"Volume '{vol_slug}' references unknown ssh-endpoint '{ref}'"
            )
            for vol_slug, vol in self.volumes.items()
            if isinstance(vol, RemoteVolume)
            for ref in [vol.ssh_endpoint, *(vol.ssh_endpoints or [])]
            if ref not in self.ssh_endpoints
        )
        yield from (
            _unknown_reference(
                f"Sync endpoint '{ep_slug}' references unknown volume '{ep.volume}'"
            )
            for ep_slug, ep in self.sync_endpoints.items()
            if ep.volume not in self.volumes
        )

    def _sync_endpoint_location_errors(self) -> Iterator[ValueError]:
        """Sync endpoints on one volume that share or nest a path."""
        candidates = (
            _location_error(slug_a, ep_a, slug_b, ep_b)
            for (slug_a, ep_a), (slug_b, ep_b) in combinations(
                self.sync_endpoints.items(), 2
            )
            if ep_a.volume == ep_b.volume
        )
        return (error for error in candidates if error is not None)

    def _sync_reference_errors(self) -> Iterator[ValueError]:
        """Syncs naming unknown source or destination endpoints."""
        return (
            _unknown_reference(
                f"Sync '{sync_slug}' references unknown {role} endpoint '{ref}'"
            )
            for sync_slug, sync in self.syncs.items()
            for role, ref in (
                ("source", sync.source),
                ("destination", sync.destination),
            )
            if ref not in self.sync_endpoints
        )

    def _shared_destination_errors(self) -> Iterator[ValueError]:
        """Pairs of syncs writing to the same destination endpoint."""
        return (
            config_error(
                ConfigValidationCode.SHARED_DESTINATION,
                f"Syncs '{slug_a}' and '{slug_b}' share destination endpoint"
                f" '{sync_a.destination}'",
            )
            for (slug_a, sync_a), (slug_b, sync_b) in combinations(
                self.syncs.items(), 2
            )
            if sync_a.destination == sync_b.destination
        )

    def _cross_server_errors(self) -> Iterator[ValueError]:
        """Remote-to-remote syncs whose volumes live on different servers."""
        volume_pairs = (
            (
                sync_slug,
                self.volumes[self.source_endpoint(sync).volume],
                self.volumes[self.destination_endpoint(sync).volume],
            )
            for sync_slug, sync in self.syncs.items()
        )
        return (
            config_error(
                ConfigValidationCode.CROSS_SERVER_SYNC,
                f"Sync '{sync_slug}' has source on '{src_vol.ssh_endpoint}'"
                f" and destination on '{dst_vol.ssh_endpoint}'."
                " Cross-server remote-to-remote syncs are not supported."
                " Use two separate syncs through the local machine instead.",
            )
            for sync_slug, src_vol, dst_vol in volume_pairs
            if isinstance(src_vol, RemoteVolume)
            and isinstance(dst_vol, RemoteVolume)
            and src_vol.ssh_endpoint != dst_vol.ssh_endpoint
        )

    def _credential_errors(self) -> Iterator[ValueError]:
        """A ``command`` provider without a command, or one lacking ``{id}``."""
        missing_command = (
            self.credential_provider == CredentialProvider.COMMAND
            and self.credential_command is None
        )
        missing_placeholder = self.credential_command is not None and not any(
            "{id}" in part for part in self.credential_command
        )
        return iter(
            [
                *(
                    [
                        config_error(
                            ConfigValidationCode.CREDENTIAL_COMMAND_REQUIRED,
                            "credential-command is required when"
                            " credential-provider is 'command'",
                        )
                    ]
                    if missing_command
                    else []
                ),
                *(
                    [
                        config_error(
                            ConfigValidationCode.CREDENTIAL_COMMAND_PLACEHOLDER,
                            "credential-command must contain the '{id}' placeholder",
                        )
                    ]
                    if missing_placeholder
                    else []
                ),
            ]
        )
