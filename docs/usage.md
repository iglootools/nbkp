# Usage

## Installation

See [Installation instructions](./installation.md)

## Quick Start

1. Create a config file at `~/.config/nbkp/config.yaml` (see [examples](#examples) below)
2. Create sentinel files on each volume:

For regular destinations (without snapshots):

```bash
# On each volume root (source and destination)
touch /mnt/data/.nbkp-vol
touch /mnt/backups/.nbkp-vol

# On source subdirectories
touch /mnt/data/photos/.nbkp-src

# On destination subdirectories (or volume root if no subdir)
mkdir -p /mnt/backups/photos
touch /mnt/backups/photos/.nbkp-dst
```

For **btrfs snapshot** destinations, create the staging subvolume and snapshots directory:

```bash
sudo btrfs subvolume create /mnt/backups/photos/staging
sudo mkdir /mnt/backups/photos/snapshots
sudo chown <user>:<group> /mnt/backups/photos/staging /mnt/backups/photos/snapshots
```

The btrfs volume must also be mounted with `user_subvol_rm_allowed` for pruning to work:

```bash
sudo mount -o remount,user_subvol_rm_allowed /mnt/backups
```

For **hard-link snapshot** destinations, create the snapshots directory:

```bash
mkdir -p /mnt/backups/photos/snapshots
```


3. Verify everything is healthy:

```bash
nbkp preflight check
```

4. Run the backup:

```bash
nbkp run
```

## Commands

See the [CLI Reference](./cli-reference.md) for the full list of commands and options.

## Config

### Config File Location

nbkp searches for config in this order:

1. Explicit `--config` path
2. `$XDG_CONFIG_HOME/nbkp/config.yaml` (typically `~/.config/nbkp/config.yaml`)
3. Platform user config dir (Linux: same as above; macOS: `~/Library/Application Support/nbkp/config.yaml`)
4. Platform site config dir (Linux: `/etc/xdg/nbkp/config.yaml`; macOS: `/Library/Application Support/nbkp/config.yaml`)

### Configuration Reference

See the [Concepts](./concepts.md) documentation for the full configuration reference.


### Example 1: Home NAS backup

A laptop backing up photos and documents to a Linux-powered NAS, with a USB drive as a secondary destination. The NAS is accessible both on the home LAN and remotely via a public hostname.

See [config-examples/home-nas-backup.yaml](config-examples/home-nas-backup.yaml).

Usage:

```bash
# At home
nbkp run --location home
nbkp run --network private

# On the road — include travel endpoints
nbkp run --location travel
nbkp run --network public

# On the road — exclude home endpoints
nbkp run --exclude-location home

# Only backup photos
nbkp run --sync photos-to-nas

# Preview what would happen
nbkp run --dry-run

# Generate a portable script for the USB drive
nbkp sh -o /mnt/usb-backup/backup.sh --relative-dst
```

### Example 2: Multi-hop chained backups

A more complex setup with a bastion host, chained syncs across local and remote volumes, mixed snapshot modes (btrfs and hard-link), and strict connection options. Data flows through a 6-step pipeline: local source, through a bastion to a remote server, across different snapshot backends, and back to a local destination.

See [config-examples/multi-hop-chain.yaml](config-examples/multi-hop-chain.yaml).

The syncs form a chain: `step-1 → step-2 → step-3 → step-4 → step-5 → step-6`. nbkp detects these dependencies automatically and runs them in order. If any step fails, all downstream steps are cancelled.

Usage:

```bash
# Run the full chain
nbkp run

# Dry-run with per-file progress
nbkp run --dry-run --progress per-file

# Prune old snapshots manually
nbkp snapshots prune --sync step-1 --sync step-3

# Diagnose issues
nbkp preflight troubleshoot

# Generate standalone script
nbkp sh -o backup.sh
```

### Example 3: Encrypted removable drives with mount management

A laptop backing up to two LUKS-encrypted external drives and one unencrypted USB drive. nbkp automatically unlocks, mounts, syncs, umounts, and locks the drives via udisks2.

See [config-examples/encrypted-removable-drives.yaml](config-examples/encrypted-removable-drives.yaml).

System setup (one-time, on the target host):

```bash
# 1. Install udisks2 (+ udisks2-btrfs for btrfs volumes) and ensure udisksd runs
sudo apt install udisks2 udisks2-btrfs   # Debian/Ubuntu

# 2. Store passphrases in keyring
keyring set nbkp seagate8tb
keyring set nbkp backup-drives

# 3. (Optional) Configure fstab for volumes with a fixed declared path.
#    No crypttab is required — udisks names the unlocked device luks-<uuid>.
# /etc/fstab:
#   /dev/mapper/luks-5941f273-f73c-44c5-a3ef-fae7248db1b6 /mnt/seagate8tb btrfs noauto,nofail,x-udisks-auth 0 0
#   /dev/mapper/luks-ad5542e5-5365-4951-a1f2-fe81c4d6fe43 /mnt/iomega1tb  ext4  noauto,nofail,x-udisks-auth 0 0
#   UUID=8a3b2c1d-...                                     /mnt/usb-plain  ext4  noauto,nofail              0 0
# Volumes whose config omits `path` need no fstab entry — udisks mounts them
# at /run/media/<user>/<label> and nbkp discovers the path at runtime.

# 4. Generate and install the polkit authorization rule
nbkp disks setup-auth -c config.yaml
# Review output, then install the single block to:
#   /etc/polkit-1/rules.d/50-nbkp.rules

# 5. Create sentinel files
touch /mnt/seagate8tb/.nbkp-vol /mnt/iomega1tb/.nbkp-vol /mnt/usb-plain/.nbkp-vol
```

See [Mount management with udisks2](#mount-management-with-udisks2) below for the full reference on fstab/crypttab combinations and mount-point resolution.

Usage:

```bash
# Run with automatic mount/umount (default)
nbkp run

# Run without mount management (volumes must be pre-mounted)
nbkp run --no-mount --no-umount

# Manually mount/umount specific volumes
nbkp disks mount --name seagate8tb
nbkp disks umount --name seagate8tb

# Diagnose mount issues
nbkp preflight troubleshoot
```

### Example 4: Sami's Personal Config

A real-world example of a complex multi-volume, multi-snapshot backup setup.

See [config-examples/personal-setup.yaml](config-examples/personal-setup.yaml).

## Mount management with udisks2

nbkp can manage the mount/umount lifecycle of removable drives (encrypted or not) so that a backup automatically unlocks, mounts, syncs, umounts, and locks each drive. All of this is driven by [udisks2](https://www.freedesktop.org/wiki/Software/udisks/) (`udisksctl`), authorized purely by polkit — there is no `sudo`, no `systemd-cryptsetup`, and no `systemctl` involved.

This is a Linux-only feature. The target host (local or remote) must have udisks2 installed and running.

### Prerequisites

1. **Install udisks2** (and `udisks2-btrfs` if any mount-managed volume holds a btrfs filesystem):

   ```bash
   sudo apt install udisks2 udisks2-btrfs        # Debian/Ubuntu
   # or: sudo dnf install udisks2 udisks2-btrfs  # Fedora
   ```

2. **Ensure the udisks daemon is running:**

   ```bash
   systemctl enable --now udisks2.service
   udisksctl status   # should list block devices
   ```

3. **Install the polkit rule.** nbkp runs over SSH or from a cron/timer in an *inactive* login session, where udisks would normally demand administrator authentication. A polkit rule grants the backup user the udisks actions unconditionally so the unattended path works. Generate and install it with:

   ```bash
   nbkp disks setup-auth -c config.yaml
   # Review the output, then install the single block to:
   #   /etc/polkit-1/rules.d/50-nbkp.rules
   ```

   The rule is the **only** authorization artifact — no sudoers file is generated. It grants the backup user the udisks actions (`filesystem-mount[-system]`, `filesystem-fstab`, `encrypted-unlock[-system]`, `encrypted-lock-others`, etc.) and is regenerated from the config so it always matches the configured volumes.

### Mount-point models: fstab × crypttab

For an encrypted volume, two independent and optional system files influence where and how it is mounted:

- **crypttab** controls the *name* of the unlocked device mapper. Without it, udisks names the unlocked device `/dev/mapper/luks-<luks-uuid>`. With a crypttab entry (`name UUID=<luks-uuid> none luks,noauto`), udisks honors the custom name. nbkp **discovers** the actual name at runtime (via `lsblk`), so both work transparently — a crypttab entry is never required.
- **fstab** controls the *mount point*. With an fstab entry mapping the device to a fixed path, udisks mounts the volume there. Without one, udisks mounts at `/run/media/<user>/<label>`.

This gives four valid combinations:

| crypttab | fstab | unlocked device | mount point | nbkp `path` |
|---|---|---|---|---|
| none | none | `/dev/mapper/luks-<uuid>` | `/run/media/<user>/<label>` (discovered) | omit |
| none | yes | `/dev/mapper/luks-<uuid>` | declared path | set |
| `name=X` | none | `/dev/mapper/X` | `/run/media/<user>/<label>` (discovered) | omit |
| `name=X` | yes | `/dev/mapper/X` | declared path | set |

Set `volume.path` **when and only when** there is an fstab entry mapping the device to a fixed path; otherwise omit it and let nbkp discover the `/run/media` mountpoint. **Consistency rule:** when both files exist, the fstab device must reference the mapper's actual name (e.g. the crypttab `name`, or `luks-<uuid>` if there is no crypttab). For unencrypted volumes crypttab is N/A — only the fstab dimension applies.

### Worked examples

**Encrypted, Option A — fixed path via fstab.** Set `path` in the config and add an fstab entry. No crypttab needed (the unlocked device defaults to `luks-<uuid>`):

```yaml
# config.yaml
volumes:
  seagate8tb:
    type: remote
    ssh-endpoint: raspberry-pi4-lan
    path: /mnt/seagate8tb
    mount:
      device-uuid: 5941f273-f73c-44c5-a3ef-fae7248db1b6
      encryption:
        type: luks
        passphrase-id: seagate8tb
```

```
# /etc/fstab on the Pi
/dev/mapper/luks-5941f273-f73c-44c5-a3ef-fae7248db1b6  /mnt/seagate8tb  ext4  noauto,nofail,x-udisks-auth  0 0
```

udisks unlocks the container, mounts it at `/mnt/seagate8tb`, and preflight verifies the fstab entry maps the device to that path (a mismatch yields `FSTAB_MOUNTPOINT_MISMATCH`).

> To keep a friendly device name like `/dev/mapper/seagate8tb`, add an optional crypttab entry `seagate8tb UUID=5941f273-... none luks,noauto` and point the fstab device at `/dev/mapper/seagate8tb` instead.

**Encrypted, Option B — discovered path via `/run/media`.** Omit `path` (and add no fstab entry). udisks mounts at `/run/media/<ssh-user>/<label>`, which nbkp discovers at runtime:

```yaml
# config.yaml
volumes:
  seagate8tb:
    type: remote
    ssh-endpoint: raspberry-pi4-lan
    mount:
      device-uuid: 5941f273-f73c-44c5-a3ef-fae7248db1b6
      encryption:
        type: luks
        passphrase-id: seagate8tb
```

This requires zero system configuration beyond the polkit rule. Sentinels, rsync paths, and snapshot directories all use the discovered mountpoint for the run.

**Unencrypted volume.** Provide only the filesystem UUID. With an fstab entry, set `path`; without one, omit it for the discovered mountpoint:

```yaml
volumes:
  usb-plain:
    type: local
    path: /mnt/usb-plain          # backed by an fstab entry; omit for /run/media
    mount:
      device-uuid: 8a3b2c1d-1111-2222-3333-444455556666
```

```
# /etc/fstab (only needed when `path` is set)
UUID=8a3b2c1d-1111-2222-3333-444455556666  /mnt/usb-plain  ext4  noauto,nofail  0 0
```

### Running

```bash
# Run with automatic mount/umount (default)
nbkp run

# Run without mount management (volumes must be pre-mounted)
nbkp run --no-mount --no-umount

# Manually mount/umount specific volumes
nbkp disks mount --name seagate8tb
nbkp disks umount --name seagate8tb

# Diagnose mount issues (udisksd down, missing polkit rule, fstab mismatch, …)
nbkp preflight troubleshoot
```

### Consequences and gotchas

- **`/run/media` is per-user.** udisks mounts unconfigured volumes under `/run/media/<user>/<label>`, where `<user>` is the account running the operation — for remote volumes, the **SSH user** on the target host. The same drive plugged into a different account lands at a different path.
- **`/run/media` is tmpfs-backed and label-derived.** The mount point is created on demand and named after the filesystem **label**; an unlabeled filesystem falls back to its UUID. If you need a stable, predictable path, use the fstab (Option A) model instead.
- **Wrong-drive safety comes from UUID + sentinel.** udisks mounts strictly by UUID, so the correct physical device is always selected regardless of where it lands. The `.nbkp-vol` sentinel then confirms the mounted content is the expected volume. There is no longer any reliance on a pre-configured systemd mount unit for this guarantee.
- **The passphrase is piped without a trailing newline.** udisks `--key-file /dev/stdin` reads the raw bytes as the key, so nbkp delivers the passphrase exactly (no newline). This is transparent to the credential provider configuration.