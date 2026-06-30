#!/bin/bash
set -e

# Set up authorized keys from mounted file
if [ -f /mnt/ssh-authorized-keys ]; then
    cp /mnt/ssh-authorized-keys /home/testuser/.ssh/authorized_keys
    chmod 600 /home/testuser/.ssh/authorized_keys
    chown testuser:testuser /home/testuser/.ssh/authorized_keys
fi

if [ -z "$NBKP_BASTION_ONLY" ]; then
    # Constants — passed via environment from Python test fixtures
    # and the demo CLI to avoid hardcoding values here.
    BACKUP_PATH="${NBKP_BACKUP_PATH:-/srv/backups}"
    BTRFS_PATH="${NBKP_BTRFS_PATH:-/srv/btrfs-backups}"

    # Persist the LUKS mapper name for setup-luks.sh. That script is
    # invoked later via `sudo` over SSH, where the container's
    # environment is NOT available: sudo strips it, and SSH sessions do
    # not inherit PID 1's environment. The mapper name is per-worker
    # unique (so concurrent privileged containers don't collide on the
    # shared /dev/mapper namespace), so it must be threaded through this
    # file rather than relying on the env reaching setup-luks.
    mkdir -p /srv
    echo "${NBKP_LUKS_MAPPER_NAME:-test-encrypted}" > /srv/luks-mapper-name

    # Detach ONLY stale loop devices left behind by previously-removed
    # containers. We must NOT run a global `losetup -D`: Docker Desktop
    # shares the kernel (and loop-device pool) across all containers, so
    # detaching every loop would yank the backing devices out from under
    # sibling containers running concurrently (e.g. under pytest-xdist).
    # A removed container's overlay filesystem is gone, so its loop's
    # backing file shows as "(deleted)"; live siblings back existing
    # files. Detaching only the deleted-backing loops reclaims the pool
    # without touching any live container, and self-heals leaks.
    losetup -ln -O NAME,BACK-FILE 2>/dev/null \
        | awk '/\(deleted\)/ {print $1}' \
        | while read -r dev; do losetup -d "$dev" 2>/dev/null || true; done

    # Pre-create a generous pool of loop-device nodes. The loop driver's
    # device pool is shared across all containers on the host VM kernel,
    # but Docker only pre-populates /dev/loop0..14 in each container's
    # private /dev, and there is no udev inside the container to create
    # nodes on demand. Under pytest-xdist, N concurrent containers each
    # need their own loop device(s); once the global pool exhausts the
    # pre-created nodes, losetup allocates a higher number (e.g. loop15)
    # whose /dev node does not exist -> "device node is lost". Creating
    # the nodes up front lets losetup --find allocate freely: opening a
    # node auto-instantiates the kernel device for that minor. The pool
    # is sized well above any realistic concurrency (each worker uses a
    # handful of loops) so that even leaked loops accumulated across many
    # local runs cannot exhaust the addressable node range before the
    # stale-loop cleanup above reclaims them. mknod is essentially free.
    for n in $(seq 0 255); do
        [ -e "/dev/loop$n" ] || mknod "/dev/loop$n" b 7 "$n" 2>/dev/null || true
    done

    # Create btrfs filesystem on a file-backed image
    BTRFS_IMG="${BTRFS_PATH}.img"
    truncate -s 256M "$BTRFS_IMG"
    mkfs.btrfs -f "$BTRFS_IMG"
    mkdir -p "$BTRFS_PATH"
    # Use mount's implicit `-o loop` rather than an explicit
    # `losetup --find` + `mount <dev>`: `mount -o loop` sets the loop
    # device's LO_FLAGS_AUTOCLEAR flag, so the device is automatically
    # detached when the filesystem is unmounted (i.e. when this container
    # stops). An explicit losetup does NOT set autoclear, which leaks one
    # loop device per container run on the shared VM kernel and, across
    # many pytest-xdist runs, eventually exhausts the global pool. The
    # pre-created node pool above gives the implicit allocator enough
    # free nodes to avoid contention at high concurrency.
    mount -o loop -o user_subvol_rm_allowed "$BTRFS_IMG" "$BTRFS_PATH"

    # Create base directories
    mkdir -p "$BACKUP_PATH"

    # Set ownership
    chown -R testuser:testuser "$BACKUP_PATH"
    chown -R testuser:testuser "$BTRFS_PATH"

    # ── LUKS-encrypted file-backed volume ───────────────────────
    # Disabled by default to speed up container startup (~5-10s).
    # Tests that need LUKS trigger setup lazily via SSH
    # (calling /setup-luks.sh), or set NBKP_LUKS_ENABLED=1 to
    # run it at startup.
    LUKS_ENABLED="${NBKP_LUKS_ENABLED:-0}"
    if [ "$LUKS_ENABLED" = "1" ]; then
        /setup-luks.sh
    else
        echo "0" > /srv/luks-available
    fi
fi

# Generate SSH host keys if not present
ssh-keygen -A

# Start sshd in foreground
echo "Server listening"
exec /usr/sbin/sshd -D -e
