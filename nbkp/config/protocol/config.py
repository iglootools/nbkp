"""
The root config model and the validation that spans its sections.

Cross-references (volumes to SSH endpoints, sync endpoints to volumes, syncs to
sync endpoints) and whole-config rules such as cycle detection are checked here,
once every section has been parsed.
"""

import enum
import re
from collections.abc import Iterable, Iterator, Mapping
from graphlib import CycleError, TopologicalSorter
from itertools import combinations
from typing import Any

from pydantic import Field, field_validator, model_validator

from nbkp.config.protocol.base import ConfigModel, is_raw_mapping
from nbkp.config.protocol.errors import ConfigValidationErrorKind, config_error
from nbkp.config.protocol.ssh_endpoint import SshEndpoint
from nbkp.config.protocol.sync import SyncConfig, sync_predecessors
from nbkp.config.protocol.sync_endpoint import SyncEndpoint
from nbkp.config.protocol.volume import RemoteVolume, Volume


class CredentialProvider(enum.StrEnum):
    """How LUKS passphrases are retrieved."""

    KEYRING = "keyring"
    PROMPT = "prompt"
    ENV = "env"
    COMMAND = "command"


def passphrase_env_var(passphrase_id: str) -> str:
    """
    Environment variable the ``env`` provider reads for ``passphrase_id``.

    Every character outside ``[A-Z0-9]`` becomes ``_``, so the name is one a
    POSIX shell can ``export``; distinct ids can then share a name, which
    config validation rejects.
    """
    return "NBKP_PASSPHRASE_" + re.sub(r"[^A-Z0-9]", "_", passphrase_id.upper())


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


# Nested sections a child overlays key by key rather than replacing, so that
# setting ``compress`` does not drop the parent's known-hosts file or timeouts.
_MERGED_SECTIONS = frozenset({"connection-options", "connection_options"})


def _overlay_field(key: str, parent_value: object, child_value: object) -> object:
    return (
        {**parent_value, **child_value}
        if key in _MERGED_SECTIONS
        and is_raw_mapping(parent_value)
        and is_raw_mapping(child_value)
        else child_value
    )


def _merge_endpoint(
    parent: Mapping[str, Any],
    child: Mapping[str, Any],
) -> dict[str, Any]:
    """
    Overlay a child endpoint's raw fields on its resolved parent's.

    The child keeps its own ``extends`` (the parent's is not inherited), so
    the merged endpoint still records which endpoint it was built from.
    """
    stale = _stale_exclusive_keys(child)
    merged = {
        **{k: v for k, v in parent.items() if k != "extends"},
        **{k: _overlay_field(k, parent.get(k), v) for k, v in child.items()},
    }
    return {k: v for k, v in merged.items() if k not in stale}


def _resolve_endpoint(
    endpoints: Mapping[str, Any],
    slug: str,
    chain: tuple[str, ...],
) -> object:
    """
    Resolve one raw endpoint's ``extends`` chain into a flat mapping.

    ``chain`` holds the slugs visited so far, ending with ``slug``, for cycle
    detection. Non-mapping entries are returned as-is for pydantic to reject.
    """
    ep = endpoints[slug]
    parent_slug = ep.get("extends") if is_raw_mapping(ep) else None
    if parent_slug is None:
        return ep
    elif parent_slug in chain:
        raise config_error(
            ConfigValidationErrorKind.CIRCULAR_EXTENDS,
            f"Circular extends chain: {' -> '.join([*chain, parent_slug])}",
        )
    elif parent_slug not in endpoints:
        raise config_error(
            ConfigValidationErrorKind.UNKNOWN_REFERENCE,
            f"Endpoint '{slug}' extends unknown endpoint '{parent_slug}'",
        )
    else:
        parent = _resolve_endpoint(endpoints, parent_slug, (*chain, parent_slug))
        return _merge_endpoint(parent, ep) if is_raw_mapping(parent) else ep


def _inject_slug(slug: str, data: object) -> object:
    match data:
        case {"slug": object() as declared} if declared != slug:
            # Sync endpoints and ``--name`` look entries up by key, endpoint
            # resolution and mount results by ``slug``: if they differed,
            # commands for one volume could reach another volume's host.
            raise config_error(
                ConfigValidationErrorKind.SLUG_MISMATCH,
                f"Entry '{slug}' declares slug {declared!r}: omit 'slug',"
                " or make it equal to the key the entry is declared under",
            )
        case _ if is_raw_mapping(data) and "slug" not in data:
            return {**data, "slug": slug}
        case _:
            return data


def _inject_slugs(entries: object) -> object:
    return (
        {slug: _inject_slug(slug, data) for slug, data in entries.items()}
        if is_raw_mapping(entries)
        else entries
    )


def _unknown_reference(message: str) -> ValueError:
    return config_error(ConfigValidationErrorKind.UNKNOWN_REFERENCE, message)


def _raise_first(errors: Iterable[ValueError]) -> None:
    """
    Raise the first error a lazy check yields, if any.

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
    slug_a: str,
    ep_a: SyncEndpoint,
    slug_b: str,
    ep_b: SyncEndpoint,
) -> ValueError | None:
    path_a, path_b = ep_a.subdir or "", ep_b.subdir or ""
    if path_a == path_b:
        subdir_msg = f" subdir '{ep_b.subdir}'" if ep_b.subdir else ""
        return config_error(
            ConfigValidationErrorKind.DUPLICATE_ENDPOINT_LOCATION,
            f"Sync endpoints '{slug_a}' and '{slug_b}' both target volume"
            f" '{ep_b.volume}'{subdir_msg}",
        )
    elif _is_nested(path_a, path_b):
        parent, child = (
            (slug_a, slug_b) if len(path_a) < len(path_b) else (slug_b, slug_a)
        )
        return config_error(
            ConfigValidationErrorKind.NESTED_ENDPOINT,
            f"Sync endpoint '{child}' is nested inside '{parent}' on volume"
            f" '{ep_a.volume}': overlapping endpoint paths are not supported"
            " because sync dependencies cannot be detected between them",
        )
    else:
        return None


class Config(ConfigModel):
    """
    A whole nbkp config: credentials, SSH endpoints, volumes, sync endpoints, syncs.

    Each section maps a slug to its entry. Validation fails with a pydantic
    ``ValidationError`` whose ``type`` is a :class:`ConfigValidationErrorKind`
    when a reference is unknown or a cross-section rule is broken.
    """

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
    def resolve_ssh_endpoint_extends(cls, data: object) -> object:
        """
        Flatten each raw SSH endpoint's ``extends`` chain before validation.

        A child endpoint inherits its parent's fields and overrides those it
        sets. Raises a ``circular-extends`` or ``unknown-reference`` error when
        the chain loops or names a missing endpoint.
        """
        if not is_raw_mapping(data):
            return data
        key = "ssh-endpoints" if "ssh-endpoints" in data else "ssh_endpoints"
        endpoints = data.get(key) or dict[str, Any]()
        if not is_raw_mapping(endpoints):
            return data
        resolved = {
            slug: _resolve_endpoint(endpoints, slug, (slug,)) for slug in endpoints
        }
        return {**data, key: resolved}

    @field_validator(
        "ssh_endpoints",
        "volumes",
        "sync_endpoints",
        "syncs",
        mode="before",
    )
    @classmethod
    def inject_slugs(cls, v: object) -> object:
        """Let entries omit ``slug``: it is the key they are declared under."""
        return _inject_slugs(v)

    def source_endpoint(self, sync: SyncConfig) -> SyncEndpoint:
        """Return the sync endpoint *sync* reads from."""
        return self.sync_endpoints[sync.source]

    def destination_endpoint(self, sync: SyncConfig) -> SyncEndpoint:
        """Return the sync endpoint *sync* writes to."""
        return self.sync_endpoints[sync.destination]

    def known_locations(self) -> list[str]:
        """Return every location any SSH endpoint declares, sorted, without repeats."""
        return sorted(
            {loc for ep in self.ssh_endpoints.values() for loc in ep.location_list},
        )

    def orphan_ssh_endpoints(self) -> list[str]:
        """
        Return the slugs of SSH endpoints nothing uses, sorted.

        An endpoint is used when a remote volume lists it, or another endpoint
        jumps through it or extends it.
        """
        used = {
            ref
            for vol in self.volumes.values()
            if isinstance(vol, RemoteVolume)
            for ref in [vol.ssh_endpoint, *(vol.ssh_endpoints or [])]
        } | {
            ref
            for ep in self.ssh_endpoints.values()
            for ref in [*ep.proxy_jump_chain, *([ep.extends] if ep.extends else [])]
        }
        return sorted(set(self.ssh_endpoints) - used)

    def orphan_volumes(self) -> list[str]:
        """Return the slugs of volumes no sync endpoint is on, sorted."""
        used = {ep.volume for ep in self.sync_endpoints.values()}
        return sorted(set(self.volumes) - used)

    def orphan_sync_endpoints(self) -> list[str]:
        """Return the slugs of sync endpoints no sync reads or writes, sorted."""
        used = {
            ref
            for sync in self.syncs.values()
            for ref in [sync.source, sync.destination]
        }
        return sorted(set(self.sync_endpoints) - used)

    @model_validator(mode="after")
    def validate_cross_references(self) -> Config:
        """
        Check the rules that span sections, and return the config unchanged.

        Raises the first failure, as a pydantic error whose ``type`` is a
        :class:`ConfigValidationErrorKind`. Checks run in order, so the later
        ones may index sections by references the earlier ones verified.
        """
        _raise_first(
            error
            for check in (
                self._unknown_proxy_jump_errors,
                self._circular_proxy_jump_errors,
                self._volume_reference_errors,
                self._sync_endpoint_location_errors,
                self._sync_reference_errors,
                self._self_sync_errors,
                self._shared_destination_errors,
                self._cyclic_dependency_errors,
                self._cross_server_errors,
                self._credential_errors,
                self._passphrase_env_var_errors,
            )
            for error in check()
        )
        return self

    def _unknown_proxy_jump_errors(self) -> Iterator[ValueError]:
        return (
            _unknown_reference(
                f"Server '{slug}' references unknown proxy-jump server '{hop}'",
            )
            for slug, server in self.ssh_endpoints.items()
            for hop in server.proxy_jump_chain
            if hop not in self.ssh_endpoints
        )

    def _circular_proxy_jump_errors(self) -> Iterator[ValueError]:
        return (
            config_error(
                ConfigValidationErrorKind.CIRCULAR_PROXY_JUMP,
                f"Circular proxy-jump chain detected starting from server '{slug}'",
            )
            for slug in self.ssh_endpoints
            if self._has_proxy_cycle(slug, frozenset())
        )

    def _has_proxy_cycle(self, slug: str, visited: frozenset[str]) -> bool:
        return slug in visited or any(
            self._has_proxy_cycle(hop, visited | {slug})
            for hop in self.ssh_endpoints[slug].proxy_jump_chain
        )

    def _volume_reference_errors(self) -> Iterator[ValueError]:
        yield from (
            _unknown_reference(
                f"Volume '{vol_slug}' references unknown ssh-endpoint '{ref}'",
            )
            for vol_slug, vol in self.volumes.items()
            if isinstance(vol, RemoteVolume)
            for ref in [vol.ssh_endpoint, *(vol.ssh_endpoints or [])]
            if ref not in self.ssh_endpoints
        )
        yield from (
            _unknown_reference(
                f"Sync endpoint '{ep_slug}' references unknown volume '{ep.volume}'",
            )
            for ep_slug, ep in self.sync_endpoints.items()
            if ep.volume not in self.volumes
        )

    def _sync_endpoint_location_errors(self) -> Iterator[ValueError]:
        candidates = (
            _location_error(slug_a, ep_a, slug_b, ep_b)
            for (slug_a, ep_a), (slug_b, ep_b) in combinations(
                self.sync_endpoints.items(),
                2,
            )
            if ep_a.volume == ep_b.volume
        )
        return (error for error in candidates if error is not None)

    def _sync_reference_errors(self) -> Iterator[ValueError]:
        return (
            _unknown_reference(
                f"Sync '{sync_slug}' references unknown {role} endpoint '{ref}'",
            )
            for sync_slug, sync in self.syncs.items()
            for role, ref in (
                ("source", sync.source),
                ("destination", sync.destination),
            )
            if ref not in self.sync_endpoints
        )

    def _self_sync_errors(self) -> Iterator[ValueError]:
        return (
            config_error(
                ConfigValidationErrorKind.SELF_SYNC,
                f"Sync '{sync_slug}' has endpoint '{sync.source}' as both its"
                " source and its destination",
            )
            for sync_slug, sync in self.syncs.items()
            if sync.source == sync.destination
        )

    def _shared_destination_errors(self) -> Iterator[ValueError]:
        return (
            config_error(
                ConfigValidationErrorKind.SHARED_DESTINATION,
                f"Syncs '{slug_a}' and '{slug_b}' share destination endpoint"
                f" '{sync_a.destination}'",
            )
            for (slug_a, sync_a), (slug_b, sync_b) in combinations(
                self.syncs.items(),
                2,
            )
            if sync_a.destination == sync_b.destination
        )

    def _cyclic_dependency_errors(self) -> Iterator[ValueError]:
        # Checked here, not only when the syncs are ordered, so that a cycle
        # is a config error reported before anything is mounted or synced.
        try:
            TopologicalSorter(sync_predecessors(self.syncs)).prepare()
        except CycleError as e:
            yield config_error(
                ConfigValidationErrorKind.CYCLIC_DEPENDENCY,
                "Cyclic sync dependency: " + " -> ".join(e.args[1]),
            )

    def _cross_server_errors(self) -> Iterator[ValueError]:
        volume_pairs = (
            (
                sync_slug,
                self.volumes[self.source_endpoint(sync).volume],
                self.volumes[self.destination_endpoint(sync).volume],
            )
            for sync_slug, sync in self.syncs.items()
        )
        # The run reaches both paths through the destination's resolved
        # endpoint. Resolution picks from each volume's candidate list on its
        # own, so only identical lists are sure to land on the same endpoint:
        # comparing the primaries alone would let a LAN/WAN pair diverge.
        return (
            config_error(
                ConfigValidationErrorKind.CROSS_SERVER_SYNC,
                f"Sync '{sync_slug}' has source on"
                f" {', '.join(src_vol.endpoint_candidates)} and destination on"
                f" {', '.join(dst_vol.endpoint_candidates)}."
                " Cross-server remote-to-remote syncs are not supported: both"
                " volumes must use the same SSH endpoints, in the same order."
                " Use two separate syncs through the local machine instead.",
            )
            for sync_slug, src_vol, dst_vol in volume_pairs
            if isinstance(src_vol, RemoteVolume)
            and isinstance(dst_vol, RemoteVolume)
            and src_vol.endpoint_candidates != dst_vol.endpoint_candidates
        )

    def _passphrase_env_var_errors(self) -> Iterator[ValueError]:
        passphrase_ids = sorted(
            {
                vol.mount.encryption.passphrase_id
                for vol in self.volumes.values()
                if vol.mount is not None and vol.mount.encryption is not None
            },
        )
        ids_by_var = {
            var: [pid for pid in passphrase_ids if passphrase_env_var(pid) == var]
            for var in dict.fromkeys(map(passphrase_env_var, passphrase_ids))
        }
        return (
            config_error(
                ConfigValidationErrorKind.PASSPHRASE_ENV_VAR_COLLISION,
                f"passphrase-ids {', '.join(repr(pid) for pid in pids)} all read"
                f" {var} with credential-provider 'env': rename all but one",
            )
            for var, pids in ids_by_var.items()
            if self.credential_provider == CredentialProvider.ENV and len(pids) > 1
        )

    def _credential_errors(self) -> Iterator[ValueError]:
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
                            ConfigValidationErrorKind.CREDENTIAL_COMMAND_REQUIRED,
                            "credential-command is required when"
                            " credential-provider is 'command'",
                        ),
                    ]
                    if missing_command
                    else []
                ),
                *(
                    [
                        config_error(
                            ConfigValidationErrorKind.CREDENTIAL_COMMAND_PLACEHOLDER,
                            "credential-command must contain the '{id}' placeholder",
                        ),
                    ]
                    if missing_placeholder
                    else []
                ),
            ],
        )
