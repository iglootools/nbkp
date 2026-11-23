#!/bin/sh
# Wait until udisks has an object for a block device path: wait-for-udisks.sh <path> [seconds]
#
# udisksd learns about block devices from udev events, asynchronously, and `udevadm settle`
# only waits for udev's own queue. Until udisks has caught up, `udisksctl unlock -b <path>`
# exits at once with "Error looking up object for device", before reading the passphrase.
# Wait on the exact path nbkp unlocks, the by-uuid link, which udisks resolves through the
# symlinks udev reported, not on the loop device alone.
#
# Bounded, and never fatal: past the limit it warns on stderr and exits 0, so a container
# that never gets there still serves the tests that do not need the device.
device="$1"
seconds="${2:-30}"
tries=0
until udisksctl info -b "$device" >/dev/null 2>&1; do
    tries=$((tries + 1))
    if [ "$tries" -ge $((seconds * 5)) ]; then
        echo "WARNING: udisks has no object for $device after ${seconds}s;" \
            "LUKS unlock will fail" >&2
        exit 0
    fi
    sleep 0.2
done
