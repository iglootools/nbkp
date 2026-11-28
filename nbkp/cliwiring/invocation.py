"""
The flags a command was invoked with, for building follow-up suggestions.

Suggested commands (``Run nbkp preflight troubleshoot …``, ``nbkp disks
setup-auth …``) must be copy-pasteable as-is: they carry the same config file
— relative to the working directory — and the same endpoint-selection flags
as the command that printed them, so the follow-up looks at the same setup.
A suggested ``preflight troubleshoot`` also keeps the strictness and leaves the
drives alone when the command did, so it judges the same problems the same way.
"""

import shlex
from dataclasses import dataclass
from pathlib import Path

from rich.text import Text

from nbkp.policy import Strictness


def display_path(path: Path, cwd: Path | None = None) -> str:
    """*path* relative to *cwd* (default: the working directory) when under it."""
    base = cwd if cwd is not None else Path.cwd()
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


@dataclass(frozen=True)
class Invocation:
    """Config path, endpoint-selection, strictness and mount flags of a command."""

    config_path: Path | None = None
    locations: tuple[str, ...] = ()
    exclude_locations: tuple[str, ...] = ()
    network: str | None = None
    """The ``--network`` value; a ``NetworkType``, which is a ``StrEnum``."""
    cwd: Path | None = None
    """Base for the relative config path; ``None`` means the working directory."""
    strictness: Strictness = Strictness.IGNORE_INACTIVE
    """The ``-S`` value, for a command that takes one."""
    mount: bool = True
    """``False`` when the command ran with ``--no-mount``."""

    @staticmethod
    def of(
        config_path: Path | None,
        locations: list[str] | None = None,
        exclude_locations: list[str] | None = None,
        network: str | None = None,
        *,
        strictness: Strictness = Strictness.IGNORE_INACTIVE,
        mount: bool = True,
    ) -> Invocation:
        """Build an invocation from the CLI options, ``None`` lists meaning none."""
        return Invocation(
            config_path=config_path,
            locations=tuple(locations or []),
            exclude_locations=tuple(exclude_locations or []),
            network=network,
            strictness=strictness,
            mount=mount,
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
        """
        Shell-quoted ``nbkp <args> -c <config> [endpoint flags]``.

        *endpoint_flags* is ``False`` for commands that take no endpoint
        selection (e.g. ``disks setup-auth``, ``credentials keyring-status``).
        """
        return shlex.join(
            [
                "nbkp",
                *args,
                *self.config_args(),
                *(self.endpoint_args() if endpoint_flags else []),
            ],
        )

    def troubleshoot_command(self) -> str:
        """
        Shell-quoted ``nbkp preflight troubleshoot`` for the same setup.

        It judges problems at the command's strictness, and leaves the drives alone
        when the command did (``--no-mount``).
        """
        return self.command(
            "preflight",
            "troubleshoot",
            *(
                ["-S", self.strictness.value]
                if self.strictness is not Strictness.IGNORE_INACTIVE
                else []
            ),
            *([] if self.mount else ["--no-mount"]),
        )


def format_troubleshoot_hint(invocation: Invocation) -> Text:
    """
    Point a failed command at ``nbkp preflight troubleshoot``.

    For commands whose JSON output is a bare list, which has no room for the
    ``hint`` field ``run`` adds to its JSON object: they print this, to
    stderr, in human mode only.  Print it with ``soft_wrap=True`` so the
    command stays on one line and can be copy-pasted.
    """
    # Text, not markup: the config path is not nbkp-authored.
    return Text.assemble(
        "Run ",
        (invocation.troubleshoot_command(), "bold"),
        " for step-by-step fixes.",
    )
