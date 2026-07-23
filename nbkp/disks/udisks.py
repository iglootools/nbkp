"""udisks2 (``udisksctl``) command builders for mount/umount and LUKS unlock/lock.

All functions are pure — they receive pre-resolved values (device paths, LUKS
UUIDs) and return command lists. No host calls at this layer.

udisks is authorized purely by polkit (no ``sudo``).  ``--no-user-interaction``
makes every call non-interactive: polkit either grants via a pre-installed rule
or denies (no prompt).  The passphrase for ``unlock`` is piped to
``--key-file /dev/stdin`` and must be written **without a trailing newline**
(udisks reads the key file's raw bytes).
"""

from __future__ import annotations

import re

# ``udisksctl unlock`` prints e.g. "Unlocked /dev/sdb1 as /dev/dm-0." — capture
# the cleartext device it created.
_UNLOCKED_AS_RE = re.compile(r"\bas\s+(/dev/\S+?)\.?\s*$", re.MULTILINE)


def parse_unlocked_device(stdout: str) -> str | None:
    """Extract the cleartext device from ``udisksctl unlock`` output.

    Returns the ``/dev/...`` path udisks reported (e.g. ``/dev/dm-0``), or
    ``None`` if the output didn't match.  Reading udisks's own output is
    race-free, unlike re-probing with ``lsblk`` immediately after unlock —
    the cleartext device node and its sysfs entry appear asynchronously, so a
    prompt ``lsblk`` can miss the crypt child.
    """
    match = _UNLOCKED_AS_RE.search(stdout)
    return match.group(1) if match else None


def cleartext_mapper_name(luks_uuid: str) -> str:
    """Default name udisks gives the unlocked device for a LUKS container.

    udisks names the cleartext device ``luks-<UUID>`` (lowercase) unless a
    ``/etc/crypttab`` entry overrides it.  Used for fstab examples and auth
    docs; runtime detection discovers the actual device rather than assuming
    this name (see :func:`disks.detection.discover_cleartext_device`).
    """
    return f"luks-{luks_uuid.lower()}"


def build_unlock_command(luks_uuid: str) -> list[str]:
    """Build command to unlock a LUKS container via udisksctl.

    Passphrase is read from stdin (``--key-file /dev/stdin``).

    Returns e.g.::

        ["udisksctl", "unlock", "-b", "/dev/disk/by-uuid/5941f273-...",
         "--key-file", "/dev/stdin", "--no-user-interaction"]
    """
    return [
        "udisksctl",
        "unlock",
        "-b",
        f"/dev/disk/by-uuid/{luks_uuid}",
        "--key-file",
        "/dev/stdin",
        "--no-user-interaction",
    ]


def build_lock_command(luks_uuid: str) -> list[str]:
    """Build command to lock a LUKS container via udisksctl.

    Returns e.g.::

        ["udisksctl", "lock", "-b", "/dev/disk/by-uuid/5941f273-...",
         "--no-user-interaction"]
    """
    return [
        "udisksctl",
        "lock",
        "-b",
        f"/dev/disk/by-uuid/{luks_uuid}",
        "--no-user-interaction",
    ]


def build_mount_command(device: str) -> list[str]:
    """Build command to mount a block device via udisksctl.

    ``device`` is the cleartext mapper (``/dev/mapper/luks-<uuid>``) for
    encrypted volumes, or ``/dev/disk/by-uuid/<fs-uuid>`` for unencrypted
    ones.  With a matching ``/etc/fstab`` entry udisks mounts at the declared
    path; otherwise at ``/run/media/<user>/<label>``.

    No ``-o`` is passed: nbkp never injects mount options.  udisks rejects any
    option not on its allowlist (``OptionNotPermitted``), so injecting e.g.
    ``user_subvol_rm_allowed`` would fail the mount on a host that hasn't
    allowlisted it.  Mount options must instead come from ``/etc/fstab`` or
    ``/etc/udisks2/mount_options.conf`` (see the mount-management internals
    docs); preflight then verifies the resulting live mount.

    Returns e.g.::

        ["udisksctl", "mount", "-b", "/dev/mapper/luks-5941f273-...",
         "--no-user-interaction"]
    """
    return ["udisksctl", "mount", "-b", device, "--no-user-interaction"]


def build_unmount_command(device: str) -> list[str]:
    """Build command to unmount a block device via udisksctl.

    Returns e.g.::

        ["udisksctl", "unmount", "-b", "/dev/mapper/luks-5941f273-...",
         "--no-user-interaction"]
    """
    return ["udisksctl", "unmount", "-b", device, "--no-user-interaction"]
