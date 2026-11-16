"""The host I/O a command performs, gathered in one object tests can replace.

The root callback installs the real :class:`Runtime` on the Typer context
unless the caller already put one there; every command reads it with
:func:`runtime_of` and hands its members to the core functions, which take
them as keyword arguments defaulting to the same real implementations.  A
test drives a whole command against fake hosts by passing its own::

    CliRunner().invoke(app, ["run", "-c", path], obj=Runtime(run=fake, ...))

Only the boundaries a command cannot avoid live here: commands on the hosts
holding the volumes, tools on the orchestrator's ``PATH``, rsync, the clock
and platform that name snapshots, and where credentials and the local user
name come from.
"""

import getpass
import os
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

import typer

from ..credentials import CommandFn, run_captured
from ..remote.dispatch import CommandLocator, VolumeCommandRunner, run_on_volume
from ..sync.modes import RsyncRunner
from ..sync.rsync import run_rsync


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Runtime:
    """The ports of a command, real by default."""

    run: VolumeCommandRunner = run_on_volume
    """Runs a command on the host holding a volume (probes, udisks, snapshots)."""
    which: CommandLocator = shutil.which
    """Finds a tool on the orchestrator's ``PATH``."""
    rsync: RsyncRunner = run_rsync
    """Runs the transfer of a sync."""
    clock: Callable[[], datetime] = utc_now
    """The current UTC time, which names snapshots."""
    platform: str = sys.platform
    """Decides the macOS-safe snapshot name form."""
    environ: Mapping[str, str] = field(default_factory=lambda: os.environ)
    """Where the ``env`` credential provider looks passphrases up."""
    run_credential_command: CommandFn = run_captured
    """Runs the ``command`` credential provider's command."""
    local_user: Callable[[], str] = getpass.getuser
    """The orchestrator user named in suggested polkit rules."""


def runtime_of(ctx: typer.Context) -> Runtime:
    """The runtime a command runs with: the one on *ctx* (a test's, or the
    real one the root callback installed), else a new real one, as when a
    sub-app runs on its own (``nbkp-demo``).
    """
    return ctx.ensure_object(Runtime)
