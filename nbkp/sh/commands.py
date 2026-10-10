"""Shell command snippets that run on a volume's host (local or over SSH)."""

from __future__ import annotations

from ..config import LocalVolume, RemoteVolume
from ..config.epresolution import ResolvedEndpoints
from ..remote.ssh import build_ssh_base_args
from .quoting import quote, quote_args, remote_command

# Preflight function return codes, interpreted by ``nbkp_run_sync`` and
# ``nbkp_enforce_strictness`` in the template.
BROKEN = 1
INACTIVE = 2
DRY_RUN_PENDING = 3


def vol_cmd(
    vol: LocalVolume | RemoteVolume,
    args: list[str],
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    """Shell command running *args* on the host that holds *vol*."""
    match vol:
        case LocalVolume():
            return quote_args(args)
        case RemoteVolume():
            ep = resolved_endpoints[vol.slug]
            ssh_args = build_ssh_base_args(ep.server, ep.proxy_chain)
            return f"{quote_args(ssh_args)} {quote(remote_command(args))}"


def which_cmd(
    vol: LocalVolume | RemoteVolume,
    command: str,
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    """Shell condition: *command* is available on the volume's host."""
    match vol:
        case LocalVolume():
            return f"command -v {quote(command)} >/dev/null 2>&1"
        case RemoteVolume():
            return f"{vol_cmd(vol, ['which', command], resolved_endpoints)} >/dev/null 2>&1"


def output_of(
    vol: LocalVolume | RemoteVolume,
    args: list[str],
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    """Double-quoted command substitution capturing stdout (never fails)."""
    return f'"$({vol_cmd(vol, args, resolved_endpoints)} 2>/dev/null || true)"'


def guard(condition: str, message: str, code: int) -> str:
    """``condition || { log message; return code; }``.

    *message* is passed to ``nbkp_log`` as a single quoted word, so paths
    inside it are never interpreted by the shell.
    """
    level = "ERROR" if code == BROKEN else "INACTIVE"
    return (
        f"{condition} || {{ nbkp_log {quote(f'{level}: {message}')}; return {code}; }}"
    )


def snapshot_date_format(vol: LocalVolume | RemoteVolume, platform: str) -> str:
    """``date`` format for snapshot names.

    Colons (ISO 8601) on Linux/remote; hyphens on macOS local volumes,
    where APFS/HFS+ forbids colons in filenames.
    """
    match vol:
        case LocalVolume() if platform == "darwin":
            return "+%Y-%m-%dT%H-%M-%S.000Z"
        case _:
            return "+%Y-%m-%dT%H:%M:%S.000Z"
