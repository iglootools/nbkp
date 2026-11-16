"""Scripted hosts for the preflight tests.

Preflight observes a host through two seams: the command runner (every probe
on a remote host, and the commands a local probe spawns) and the local tool
lookup.  :func:`healthy_host` scripts the runner the way a healthy host
answers; a test prepends the one answer its scenario changes::

    run = healthy_host(
        FakeCommandRunner().on("stat", "-c", "%i", returns=completed(stdout="5\\n")),
        fs_type="btrfs",
    )
    check_sync(sync, config, run=run, which=local_tools())

Local sentinels, directories and symlinks are read from the real filesystem
(``tmp_path``); on a remote host, unscripted ``test`` probes succeed.
"""

from collections.abc import Collection

from nbkp.remote.dispatch import CommandLocator
from tests.fakes import FakeCommandRunner, completed

GNU_RSYNC_VERSION = "rsync  version 3.2.7  protocol version 31\n"
HEALTHY_MOUNT_OPTIONS = "rw,relatime,user_subvol_rm_allowed"
FAILS = completed(returncode=1)
"""The answer of a probe that says no (``which``, ``test``) or fails."""


def local_tools(*, missing: Collection[str] = ()) -> CommandLocator:
    """The orchestrator's ``PATH``: every tool but *missing*."""
    return lambda tool: None if tool in missing else f"/usr/bin/{tool}"


def healthy_host(
    run: FakeCommandRunner | None = None,
    *,
    fs_type: str = "ext2/ext3",
) -> FakeCommandRunner:
    """*run* (a new runner by default) answering what its rules leave out as a
    healthy host does.

    GNU rsync 3.2.7, volume roots on *fs_type* mounted with
    ``user_subvol_rm_allowed``, btrfs staging directories that are
    subvolumes, and ``latest`` symlinks pointing to ``/dev/null``.  Every
    other command — ``which`` and ``test`` among them — succeeds.  The rules
    are appended, so the ones *run* already has take precedence.
    """
    return (
        (run or FakeCommandRunner())
        .on("rsync", "--version", returns=completed(stdout=GNU_RSYNC_VERSION))
        .on("stat", "-f", "-c", "%T", returns=completed(stdout=f"{fs_type}\n"))
        .on("findmnt", "-T", returns=completed(stdout=f"{HEALTHY_MOUNT_OPTIONS}\n"))
        .on("stat", "-c", "%i", returns=completed(stdout="256\n"))
        .on("readlink", returns=completed(stdout="/dev/null\n"))
    )
