"""Volume availability checks."""

from __future__ import annotations

from pathlib import Path

from ..config import (
    LocalVolume,
    RemoteVolume,
    ResolvedEndpoints,
    Volume,
)
from ..remote import run_remote_command
from .status import VolumeReason, VolumeStatus


def check_volume(
    volume: Volume,
    resolved_endpoints: ResolvedEndpoints | None = None,
) -> VolumeStatus:
    """Check if a volume is active."""
    re = resolved_endpoints or {}
    match volume:
        case LocalVolume():
            return _check_local_volume(volume)
        case RemoteVolume():
            return _check_remote_volume(volume, re)


def _check_local_volume(volume: LocalVolume) -> VolumeStatus:
    """Check if a local volume is active (.nbkp-vol sentinel exists)."""
    sentinel = Path(volume.path) / ".nbkp-vol"
    reasons: list[VolumeReason] = (
        [] if sentinel.exists() else [VolumeReason.SENTINEL_NOT_FOUND]
    )
    return VolumeStatus(
        slug=volume.slug,
        config=volume,
        reasons=reasons,
    )


def _check_remote_volume(
    volume: RemoteVolume,
    resolved_endpoints: ResolvedEndpoints,
) -> VolumeStatus:
    """Check if a remote volume is active (SSH + .nbkp-vol sentinel)."""
    if volume.slug not in resolved_endpoints:
        return VolumeStatus(
            slug=volume.slug,
            config=volume,
            reasons=[VolumeReason.LOCATION_EXCLUDED],
        )
    ep = resolved_endpoints[volume.slug]
    sentinel_path = f"{volume.path}/.nbkp-vol"
    try:
        result = run_remote_command(
            ep.server, ["test", "-f", sentinel_path], ep.proxy_chain
        )
        reasons: list[VolumeReason] = (
            [] if result.returncode == 0 else [VolumeReason.UNREACHABLE]
        )
    except Exception:
        reasons = [VolumeReason.UNREACHABLE]
    return VolumeStatus(
        slug=volume.slug,
        config=volume,
        reasons=reasons,
    )
