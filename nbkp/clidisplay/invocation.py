"""The flags a command was invoked with, for building follow-up suggestions.

Suggested commands (``Run nbkp preflight troubleshoot …``, ``nbkp disks
setup-auth …``) must be copy-pasteable as-is: they carry the same config file
— relative to the working directory — and the same endpoint-selection flags
as the command that printed them, so the follow-up looks at the same setup.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path


def display_path(path: Path, cwd: Path | None = None) -> str:
    """*path* relative to *cwd* (default: the working directory) when under it."""
    base = cwd if cwd is not None else Path.cwd()
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


@dataclass(frozen=True)
class Invocation:
    """Config path and endpoint-selection flags of the current command."""

    config_path: Path | None = None
    locations: tuple[str, ...] = ()
    exclude_locations: tuple[str, ...] = ()
    network: str | None = None
    cwd: Path | None = None
    """Base for the relative config path; ``None`` means the working directory."""

    @staticmethod
    def of(
        config_path: Path | None,
        locations: list[str] | None = None,
        exclude_locations: list[str] | None = None,
        network: str | None = None,
    ) -> Invocation:
        """Build from Typer option values."""
        return Invocation(
            config_path=config_path,
            locations=tuple(locations or []),
            exclude_locations=tuple(exclude_locations or []),
            network=network,
        )

    def config_args(self) -> list[str]:
        """``-c <config>`` when an explicit config file was given."""
        return (
            ["-c", display_path(self.config_path, self.cwd)]
            if self.config_path is not None
            else []
        )

    def endpoint_args(self) -> list[str]:
        """``-l`` / ``-L`` / ``-N`` flags, in that order."""
        return [
            *(arg for loc in self.locations for arg in ("-l", loc)),
            *(arg for loc in self.exclude_locations for arg in ("-L", loc)),
            *(["-N", self.network] if self.network is not None else []),
        ]

    def command(self, *args: str, endpoint_flags: bool = True) -> str:
        """Shell-quoted ``nbkp <args> -c <config> [endpoint flags]``.

        *endpoint_flags* is ``False`` for commands that take no endpoint
        selection (e.g. ``disks setup-auth``, ``credentials keyring-status``).
        """
        return shlex.join(
            [
                "nbkp",
                *args,
                *self.config_args(),
                *(self.endpoint_args() if endpoint_flags else []),
            ]
        )
