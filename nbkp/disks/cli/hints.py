"""Hints the ``disks`` lifecycle commands print after a failed step."""

from collections.abc import Sequence

from rich.console import Console
from rich.text import Text

from nbkp.cliwiring.invocation import Invocation
from nbkp.disks.lifecycle import MountResult, UmountResult, refused_by_polkit


def print_auth_hint(
    results: Sequence[MountResult | UmountResult],
    invocation: Invocation,
) -> None:
    """Point at ``disks setup-auth`` when polkit refused a lifecycle step."""
    if refused_by_polkit(results):
        console = Console()
        console.print(
            "udisks refused the operation (no polkit rule). Generate one with:",
        )
        # soft_wrap: the command must stay on one line to copy-paste.
        console.print(
            Text(
                invocation.command("disks", "setup-auth", endpoint_flags=False),
                style="bold",
            ),
            soft_wrap=True,
        )
