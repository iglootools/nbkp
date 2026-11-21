"""The host I/O a command performs, gathered in one object tests can replace.

The root callback installs the real :class:`Runtime` on the Typer context
unless the caller already put one there; every command reads it with
:func:`runtime_of` and hands its members to the core functions, which take
them as keyword arguments defaulting to the same real implementations.  A
test drives a whole command against fake hosts by passing its own::

    CliRunner().invoke(app, ["run", "-c", path], obj=Runtime(run=fake, ...))

Only the boundaries a command cannot avoid live here: commands on the hosts
holding the volumes, tools on the orchestrator's ``PATH``, rsync, the clock
and platform that name snapshots, where the config file is searched for, and
where credentials and the local user name come from, and the lock that
keeps two commands touching the drives and destinations apart.
"""

import getpass
import os
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import typer
from platformdirs import site_config_dir, user_config_dir

from ..config import Config, load_config
from ..config.loader import ConfigDirFn
from ..credentials import CommandFn, run_captured
from ..remote.dispatch import VolumeCommandRunner, WhichFn, run_on_volume
from ..sync.modes import RsyncRunner
from ..sync.rsync import run_rsync
from .lock import RunLockFn, hold_run_lock, resolve_run_lock_path


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Runtime:
    """The ports of a command, real by default."""

    run: VolumeCommandRunner = run_on_volume
    """Runs a command on the host holding a volume (probes, udisks, snapshots)."""
    which: WhichFn = shutil.which
    """Finds a tool on the orchestrator's ``PATH``."""
    rsync: RsyncRunner = run_rsync
    """Runs the transfer of a sync."""
    clock: Callable[[], datetime] = utc_now
    """The current UTC time, which names snapshots."""
    platform: str = sys.platform
    """Decides the macOS-safe snapshot name form."""
    environ: Mapping[str, str] = field(default_factory=lambda: os.environ)
    """The process environment: where the ``env`` credential provider looks
    passphrases up, and ``XDG_CONFIG_HOME`` for the config search."""
    home: Callable[[], Path] = Path.home
    """The user's home directory, where the config search starts."""
    user_config_dir: ConfigDirFn = user_config_dir
    """The platform user config directory of an app."""
    site_config_dir: ConfigDirFn = site_config_dir
    """The platform site config directory of an app."""
    run_credential_command: CommandFn = run_captured
    """Runs the ``command`` credential provider's command."""
    local_user: Callable[[], str] = getpass.getuser
    """The orchestrator user named in suggested polkit rules."""
    run_lock: RunLockFn = hold_run_lock
    """Holds the lock that keeps two commands touching the drives and
    destinations apart (``run``, ``disks mount``, …), at the path
    :meth:`run_lock_path` gives."""

    def run_lock_path(self) -> Path:
        """The run lock under this runtime's ``XDG_STATE_HOME`` or home."""
        return resolve_run_lock_path(self.environ, self.home())

    def load_config(self, config_path: str | Path | None) -> Config:
        """Load *config_path*, or search for the config in this runtime's
        environment, home and platform directories."""
        return load_config(
            config_path,
            self.environ,
            home=self.home(),
            user_dir=self.user_config_dir,
            site_dir=self.site_config_dir,
        )


def runtime_of(ctx: typer.Context) -> Runtime:
    """The runtime a command runs with: the one on *ctx* (a test's, or the
    real one the root callback installed), else a new real one, as when a
    sub-app runs on its own (``nbkp-demo``).
    """
    return ctx.ensure_object(Runtime)
