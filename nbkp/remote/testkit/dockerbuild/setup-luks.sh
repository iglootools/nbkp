#!/bin/bash
# Set up a LUKS-encrypted file-backed btrfs volume for testing.
#
# Idempotent: exits immediately if LUKS was already set up
# (checks /srv/luks-available marker).
#
# Requires: cryptsetup, dm-crypt kernel module, privileged container.
# Called lazily by test fixtures that need encryption, or at container
# startup when NBKP_LUKS_ENABLED=1.
set -e

# Disable device-mapper udev synchronization for every cryptsetup/dmsetup call
# in this script.  cryptsetup normally waits on a udev "cookie" after open/close
# so udev can create/remove the /dev/mapper node; when the container shares the
# host /dev (needed so udisks can probe filesystems at runtime) that wait can
# deadlock against the shared udevd, hanging `cryptsetup close` indefinitely.
# We create the format device node by hand (mknod, below), so udev sync is
# unnecessary here.  This affects only this script — the runtime udisks daemon
# manages its own devices independently.
export DM_DISABLE_UDEV=1

# Already set up?
if [ -f /srv/luks-available ] && [ "$(cat /srv/luks-available)" = "1" ]; then
    exit 0
fi

# Treat ANY setup failure as "LUKS unavailable" rather than a hard error:
# on a shared-kernel Docker Desktop host, loop/dm allocation is flaky and a
# transient failure should make dependent tests *skip*, not error.  The ERR
# trap records the partial state (0 unless we reached the very end) and exits
# 0 so the calling fixture sees a clean run.
trap 'echo "${NBKP_LUKS_AVAILABLE:-0}" > /srv/luks-available; exit 0' ERR

BTRFS_ENCRYPTED_PATH="${NBKP_BTRFS_ENCRYPTED_PATH:-/srv/btrfs-encrypted-backups}"
LUKS_PASSPHRASE="${NBKP_LUKS_PASSPHRASE:-test-passphrase}"
# Per-worker-unique mapper name. This script is usually invoked via `sudo`
# over SSH, where NBKP_LUKS_MAPPER_NAME is NOT in the environment (sudo
# strips it; SSH sessions don't inherit PID 1's env), so fall back to the
# file persisted by entrypoint.sh at startup.  It is used only as the
# transient name under which we open the container to create the btrfs
# filesystem below; nbkp re-opens it at runtime via udisks as
# `luks-<uuid>` (the real cleartext device — see the fstab entry).  Being
# per-worker-unique, it never collides in the shared kernel dm table with
# the concurrent containers pytest-xdist spins up.
LUKS_MAPPER="${NBKP_LUKS_MAPPER_NAME:-$(cat /srv/luks-mapper-name 2>/dev/null || echo test-encrypted)}"

# Close only THIS worker's own (per-worker-unique) mapper, in case a prior
# run by the same worker left it behind.  Must NOT blanket-close every
# `luks-*` mapper via `dmsetup ls`: the kernel dm table is shared across
# containers, so that would tear down sibling workers' live devices under
# pytest-xdist.  Stale-loop reclamation is handled once, safely, in
# entrypoint.sh before anything is mounted.
cryptsetup close "$LUKS_MAPPER" 2>/dev/null || true

NBKP_LUKS_AVAILABLE=0
if command -v cryptsetup >/dev/null 2>&1; then
    LUKS_IMG="${BTRFS_ENCRYPTED_PATH}.img"
    truncate -s 128M "$LUKS_IMG"
    # losetup may fail if loop device nodes are missing (Docker
    # Desktop on macOS).  Protect with || true since LUKS is
    # optional — tests skip when unavailable.
    LOOP_DEV=$(losetup --find --show "$LUKS_IMG" 2>/dev/null) || true
    if [ -n "$LOOP_DEV" ] && echo -n "$LUKS_PASSPHRASE" | cryptsetup luksFormat \
            --batch-mode --pbkdf pbkdf2 --pbkdf-force-iterations 1000 \
            "$LOOP_DEV" - 2>/dev/null; then
        LUKS_UUID=$(cryptsetup luksUUID "$LOOP_DEV")

        # Create /dev/disk/by-uuid symlink (fallback; udev normally maintains it)
        mkdir -p /dev/disk/by-uuid
        ln -sf "$LOOP_DEV" "/dev/disk/by-uuid/$LUKS_UUID"

        # Re-trigger udev so udisksd enumerates the new LUKS loop device —
        # without this udisks has no D-Bus object for it and unlock fails.
        udevadm trigger "$LOOP_DEV" 2>/dev/null || udevadm trigger 2>/dev/null || true
        udevadm settle 2>/dev/null || true

        # Open LUKS under the transient per-worker name, format as btrfs,
        # set ownership, close — nbkp re-opens later via udisks (as
        # luks-<uuid>).
        echo -n "$LUKS_PASSPHRASE" | cryptsetup open \
            --type luks "$LOOP_DEV" "$LUKS_MAPPER" -
        # Docker's /dev is a minimal tmpfs (not devtmpfs), so the dm device
        # node is never auto-created and udev doesn't run reliably — mkfs
        # would fail with "No such file or directory".  Create the node
        # explicitly from the major:minor that dmsetup reports.
        udevadm settle 2>/dev/null || true
        FMT_DEV="/dev/mapper/$LUKS_MAPPER"
        if [ ! -b "$FMT_DEV" ]; then
            # May be a dangling symlink to a non-existent /dev/dm-N — replace
            # it with a real block node from dmsetup's major:minor.
            _mm=$(dmsetup info -c --noheadings -o major,minor "$LUKS_MAPPER")
            mkdir -p /dev/mapper
            rm -f "$FMT_DEV"
            mknod "$FMT_DEV" b "${_mm%%:*}" "${_mm##*:}"
        fi
        mkfs.btrfs -f "$FMT_DEV"

        # Create mount point, mount, prepare for testuser, umount
        mkdir -p "$BTRFS_ENCRYPTED_PATH"
        mount -o user_subvol_rm_allowed "$FMT_DEV" "$BTRFS_ENCRYPTED_PATH"
        chown testuser:testuser "$BTRFS_ENCRYPTED_PATH"
        umount "$BTRFS_ENCRYPTED_PATH"
        cryptsetup close "$LUKS_MAPPER" 2>/dev/null \
            || dmsetup remove -f "$LUKS_MAPPER" 2>/dev/null || true

        # Add an fstab entry so udisks mounts the volume at the fixed path
        # (Option A — fixed-path model).  When nbkp unlocks the container
        # via `udisksctl unlock`, udisks names the cleartext device
        # `luks-<uuid>` (lowercase, no crypttab), so the fstab device must
        # reference that name.  x-udisks-auth + noauto/nofail mark it as a
        # udisks-managed, non-boot mount.
        LUKS_UUID_LC=$(echo "$LUKS_UUID" | tr 'A-Z' 'a-z')
        echo "/dev/mapper/luks-${LUKS_UUID_LC} $BTRFS_ENCRYPTED_PATH btrfs user_subvol_rm_allowed,noauto,nofail,x-udisks-auth 0 0" \
            >> /etc/fstab

        # Save metadata for tests to read via SSH
        echo "$LUKS_UUID" > /srv/luks-uuid
        echo "$LOOP_DEV" > /srv/luks-loop-device

        # Install the polkit rule that grants testuser the udisks actions
        # nbkp needs over SSH (inactive session) — this mirrors what
        # `nbkp disks setup-auth --user testuser` generates.  udisks is
        # polkit-only: no sudoers entry is required for the mount path.
        mkdir -p /etc/polkit-1/rules.d
        cat > /etc/polkit-1/rules.d/50-nbkp.rules <<'POLKIT'
// Installed by nbkp test container (mirrors `nbkp disks setup-auth`)
var nbkpActions = [
    "org.freedesktop.udisks2.filesystem-mount",
    "org.freedesktop.udisks2.filesystem-mount-system",
    "org.freedesktop.udisks2.filesystem-mount-other-seat",
    "org.freedesktop.udisks2.filesystem-fstab",
    "org.freedesktop.udisks2.filesystem-unmount-others",
    "org.freedesktop.udisks2.encrypted-unlock",
    "org.freedesktop.udisks2.encrypted-unlock-system",
    "org.freedesktop.udisks2.encrypted-lock-others"
];
polkit.addRule(function(action, subject) {
    if (subject.user == "testuser" &&
        nbkpActions.indexOf(action.id) > -1) {
        return polkit.Result.YES;
    }
});
POLKIT
        chmod 644 /etc/polkit-1/rules.d/50-nbkp.rules

        NBKP_LUKS_AVAILABLE=1

        # ── Plain unencrypted ext4 volume (udisks unencrypted-mount test) ──
        # Exercises the non-LUKS udisks path: device_uuid is the filesystem
        # UUID and nbkp mounts it directly (no unlock).  Best-effort and
        # NON-fatal: Docker Desktop's loop allocation is flaky for secondary
        # devices, so guard every step — if it fails the marker file is left
        # absent and the unencrypted test skips (LUKS tests are unaffected).
        UNENC_PATH="/srv/unencrypted-backups"
        UNENC_IMG="${UNENC_PATH}.img"
        if truncate -s 64M "$UNENC_IMG" 2>/dev/null \
                && UNENC_LOOP=$(losetup --find --show "$UNENC_IMG" 2>/dev/null) \
                && [ -n "$UNENC_LOOP" ] \
                && mkfs.ext4 -q "$UNENC_LOOP" 2>/dev/null; then
            UNENC_UUID=$(blkid -s UUID -o value "$UNENC_LOOP")
            ln -sf "$UNENC_LOOP" "/dev/disk/by-uuid/$UNENC_UUID"
            mkdir -p "$UNENC_PATH"
            mount "$UNENC_LOOP" "$UNENC_PATH"
            chown testuser:testuser "$UNENC_PATH"
            umount "$UNENC_PATH"
            echo "UUID=${UNENC_UUID} $UNENC_PATH ext4 noauto,nofail,x-udisks-auth 0 0" \
                >> /etc/fstab
            udevadm trigger "$UNENC_LOOP" 2>/dev/null || true
            udevadm settle 2>/dev/null || true
            echo "$UNENC_UUID" > /srv/unencrypted-uuid
        fi
    else
        losetup -d "$LOOP_DEV" 2>/dev/null || true
        rm -f "$LUKS_IMG"
    fi
fi
echo "$NBKP_LUKS_AVAILABLE" > /srv/luks-available
