# r/Backup Intro Post

## Title

I wrote Nomad Backup (nbkp) to replace my rsync shell scripts -- here's why

## Body

For years I've been backing up my Mac OS X laptop, a handful of removable USB drives, and a Raspberry Pi home server using a growing collection of rsync shell scripts. 

You probably know the drill: a script per backup pair, hardcoded paths, SSH options copy-pasted between files,
and a gnawing fear that one day you'll sync from an empty mount point and wipe the good copy.

It worked. Mostly. But the scripts kept getting more complicated:

- I travel, so my Pi is sometimes reachable over the LAN, sometimes through the internet/VPN tunnel, and sometimes not at all. 
  Each script needed to figure out which SSH endpoint to use.
- I added btrfs snapshots on the Pi so a bad sync couldn't destroy the only copy. 
  That meant more shell logic for snapshot creation, symlink management, and pruning old snapshots.
- I maintain multiple copies of the same data for redundancy, some on btrfs (for snapshot support, only available from Linux) 
  and some on non-btrfs filesystems (to keep with me so I can keep backing up important data while traveling). 
  This means having to detect which drive is mounted on the laptop and on the Pi to decide which backups to perform.
- Downstream syncs depend on upstream syncs as the backups need to be propagated to multiple drives 
  (from laptop: USB drive 1 (local) -> USB drive 2 (Pi), and then from Pi: USB drive 2 (Pi) -> USB drive 3 (Pi)), so ordering and error propagation mattered. 

I did not find any backup tools that I liked. And honestly, I don't even understand what half of these tools do and if they could be adapted to my needs.
Many felt too complex and enterprise-y for my needs, while others were too simple and lacked features like snapshots or dependency management.
One thing in particular that I do not like with a lot of solutions is the reliance on opaque storage formats and custom snapshot management, 
when btrfs provides an efficient and user-friendly solution where snapshots are simple directory trees.

I eventually extracted the patterns into a tool: **[nbkp](https://github.com/iglootools/nbkp)** (Nomad Backup).
It's rsync + SSH under the hood, stores files as plain directories (no proprietary format -- restoring is just a copy), 
and is designed for exactly this kind of setup where sources and destinations aren't always available.

### What it does

One YAML config describes all your volumes, SSH endpoints, and syncs. `nbkp run` does the rest:

- **Sentinel files** (`.nbkp-vol`, `.nbkp-src`, `.nbkp-dst`) guard every volume and endpoint. 
  If a USB drive isn't plugged in or a server isn't reachable, the sync is skipped -- not blindly executed against an empty mount point.
- **Btrfs snapshots** or **hard-link snapshots** (for non-btrfs filesystems) give you point-in-time recovery. 
  The `latest` symlink only moves forward after a successful sync, so a failed run can't corrupt your snapshot chain.
- **Automatic dependency ordering** If sync A's destination is sync B's source, A runs first. If A fails, B (and anything downstream) is cancelled automatically.
- **Location-aware SSH endpoints** Tag endpoints with a location (`home`, `travel`, etc.) and let nbkp pick the right one based on where you are;
  or skips the volume entirely when you tell it you're not on that network.
- **Pre-flight checks** verify everything before transferring a single byte: sentinel files, SSH connectivity, rsync version, btrfs filesystem type and mount options, directory permissions.
- **`nbkp sh`** spits out a standalone bash script you can drop on a headless box -- no Python needed. 
  Also handy if you just want to eyeball the actual commands before letting anything touch your data.

### What it doesn't do

- No Windows support -- Haven't used it for decades, so no idea wether Cygwin is still a thing, if WSL could work, 
  or if there is a completely different rsync-like ecosystem that's the preferred way of performing backups.
- No cloud backends -- local and SSH only. Might explore [rclone](https://rclone.org/) integration down the road.
- No built-in encryption -- just slap a LUKS volume underneath.
  I'd like to integrate LUKS management someday so you don't have to manually unlock and mount every time, but that's out of scope for now.
- No bidirectional sync -- one-way rsync, on purpose. It could be interesting to explore [unison](https://www.cis.upenn.edu/~bcpierce/unison/) 
  integration for bidirectional syncs, but I don't have a use case for it for now.
- Not designed for always-on server-to-server replication (though you can deploy a generated shell script on a server for that).

### My setup

To illustrate with a concrete example, here is my personal [config.yaml](https://github.com/iglootools/nbkp/blob/main/marketing/r-backup-intro/config.yaml). 

`nbkp config graph` outputs a visualization of the sync topology, which looks like this:

```
laptop-docs
├── laptop-docs-to-rocketnano1tb -> rocketnano1tb-backups-docs (hard-link, max: 10)
├── laptop-docs-to-rocketnano2tb -> rocketnano2tb-backups-docs (hard-link, max: 10)
└── laptop-docs-to-seagate8tb -> seagate8tb-backups-docs (btrfs, max: 30)
    ├── seagate8tb-backups-docs-to-seagate1tb -> seagate1tb-backups-docs (btrfs, max: 30)
    ├── seagate8tb-backups-docs-to-seagate2tb -> seagate2tb-backups-docs (btrfs, max: 30)
    └── seagate8tb-backups-docs-to-wd6tb -> wd6tb-backups-docs (btrfs, max: 30)
laptop-home
└── laptop-home-to-seagate8tb -> seagate8tb-backups-home (btrfs, max: 30)
    └── seagate8tb-backups-home-to-wd6tb -> wd6tb-backups-home (btrfs, max: 30)
rocketnano1tb-applications
└── rocketnano1tb-applications-to-seagate8tb -> seagate8tb-backups-applications (btrfs, max: 30)
    ├── seagate8tb-backups-applications-to-seagate1tb -> seagate1tb-backups-applications (btrfs, max: 30)
    └── seagate8tb-backups-applications-to-wd6tb -> wd6tb-backups-applications (btrfs, max: 30)
rocketnano1tb-audio
└── rocketnano1tb-audio-to-seagate8tb -> seagate8tb-backups-audio (btrfs, max: 30)
    ├── seagate8tb-backups-audio-to-seagate1tb -> seagate1tb-backups-audio (btrfs, max: 30)
    └── seagate8tb-backups-audio-to-wd6tb -> wd6tb-backups-audio (btrfs, max: 30)
rocketnano1tb-education
└── rocketnano1tb-education-to-seagate8tb -> seagate8tb-backups-education (btrfs, max: 30)
    ├── seagate8tb-backups-education-to-seagate1tb -> seagate1tb-backups-education (btrfs, max: 30)
    └── seagate8tb-backups-education-to-wd6tb -> wd6tb-backups-education (btrfs, max: 30)
rocketnano2tb-photos
└── rocketnano2tb-photos-to-seagate8tb -> seagate8tb-backups-photos (btrfs, max: 30)
    ├── seagate8tb-backups-photos-to-seagate2tb -> seagate2tb-backups-photos (btrfs, max: 30)
    └── seagate8tb-backups-photos-to-wd6tb -> wd6tb-backups-photos (btrfs, max: 30)
```

A different way to visualize the config in a way that puts less emphasis on the graph structure is `nbkp config show`:

```
                                   SSH Endpoints:
┏━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━┳━━━━━━┳━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━┓
┃ Name              ┃ Host             ┃ Port ┃ User ┃ Key ┃ Proxy Jump ┃ Locations ┃
┡━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━╇━━━━━━╇━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━┩
│ raspberry-pi4-lan │ 10.0.0.43        │ 22   │      │     │            │ home      │
│ raspberry-pi4-wan │ sami.example.com │ 22   │      │     │            │ travel    │
└───────────────────┴──────────────────┴──────┴──────┴─────┴────────────┴───────────┘

                                 Volumes:
┏━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Name          ┃ Type   ┃ SSH Endpoint      ┃ URI                       ┃
┡━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ laptop-docs   │ local  │                   │ /Volumes/docs             │
│ laptop-home   │ local  │                   │ /Users/samidalouche       │
│ rocketnano1tb │ local  │                   │ /Volumes/rocketnano1tb    │
│ rocketnano2tb │ local  │                   │ /Volumes/rocketnano2tb    │
│ seagate1tb    │ remote │ raspberry-pi4-lan │ 10.0.0.43:/mnt/seagate1tb │
│ seagate2tb    │ remote │ raspberry-pi4-lan │ 10.0.0.43:/mnt/seagate2tb │
│ seagate8tb    │ remote │ raspberry-pi4-lan │ 10.0.0.43:/mnt/seagate8tb │
│ wd6tb         │ remote │ raspberry-pi4-lan │ 10.0.0.43:/mnt/wd6tb      │
└───────────────┴────────┴───────────────────┴───────────────────────────┘

                                                                                 Syncs:
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ Name                                          ┃ Source                           ┃ Destination                      ┃ Options                               ┃ Enabled ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━┩
│ laptop-docs-to-rocketnano1tb                  │ laptop-docs                      │ rocketnano1tb:/backups/docs      │ hard-link-snapshots(max:10)           │ yes     │
│ laptop-docs-to-rocketnano2tb                  │ laptop-docs                      │ rocketnano2tb:/backups/docs      │ hard-link-snapshots(max:10)           │ yes     │
│ laptop-docs-to-seagate8tb                     │ laptop-docs                      │ seagate8tb:/backups/docs         │ btrfs-snapshots(max:30)               │ yes     │
│ laptop-home-to-seagate8tb                     │ laptop-home                      │ seagate8tb:/backups/home         │ rsync-filter, btrfs-snapshots(max:30) │ yes     │
│ rocketnano1tb-applications-to-seagate8tb      │ rocketnano1tb:/applications      │ seagate8tb:/backups/applications │ btrfs-snapshots(max:30)               │ yes     │
│ rocketnano1tb-audio-to-seagate8tb             │ rocketnano1tb:/audio             │ seagate8tb:/backups/audio        │ btrfs-snapshots(max:30)               │ yes     │
│ rocketnano1tb-education-to-seagate8tb         │ rocketnano1tb:/education         │ seagate8tb:/backups/education    │ btrfs-snapshots(max:30)               │ yes     │
│ rocketnano2tb-photos-to-seagate8tb            │ rocketnano2tb:/photos            │ seagate8tb:/backups/photos       │ btrfs-snapshots(max:30)               │ yes     │
│ seagate8tb-backups-applications-to-seagate1tb │ seagate8tb:/backups/applications │ seagate1tb:/backups/applications │ src:btrfs, btrfs-snapshots(max:30)    │ yes     │
│ seagate8tb-backups-applications-to-wd6tb      │ seagate8tb:/backups/applications │ wd6tb:/backups/applications      │ src:btrfs, btrfs-snapshots(max:30)    │ yes     │
│ seagate8tb-backups-audio-to-seagate1tb        │ seagate8tb:/backups/audio        │ seagate1tb:/backups/audio        │ src:btrfs, btrfs-snapshots(max:30)    │ yes     │
│ seagate8tb-backups-audio-to-wd6tb             │ seagate8tb:/backups/audio        │ wd6tb:/backups/audio             │ src:btrfs, btrfs-snapshots(max:30)    │ yes     │
│ seagate8tb-backups-docs-to-seagate1tb         │ seagate8tb:/backups/docs         │ seagate1tb:/backups/docs         │ src:btrfs, btrfs-snapshots(max:30)    │ yes     │
│ seagate8tb-backups-docs-to-seagate2tb         │ seagate8tb:/backups/docs         │ seagate2tb:/backups/docs         │ src:btrfs, btrfs-snapshots(max:30)    │ yes     │
│ seagate8tb-backups-docs-to-wd6tb              │ seagate8tb:/backups/docs         │ wd6tb:/backups/docs              │ src:btrfs, btrfs-snapshots(max:30)    │ yes     │
│ seagate8tb-backups-education-to-seagate1tb    │ seagate8tb:/backups/education    │ seagate1tb:/backups/education    │ src:btrfs, btrfs-snapshots(max:30)    │ yes     │
│ seagate8tb-backups-education-to-wd6tb         │ seagate8tb:/backups/education    │ wd6tb:/backups/education         │ src:btrfs, btrfs-snapshots(max:30)    │ yes     │
│ seagate8tb-backups-home-to-wd6tb              │ seagate8tb:/backups/home         │ wd6tb:/backups/home              │ src:btrfs, btrfs-snapshots(max:30)    │ yes     │
│ seagate8tb-backups-photos-to-seagate2tb       │ seagate8tb:/backups/photos       │ seagate2tb:/backups/photos       │ src:btrfs, btrfs-snapshots(max:30)    │ yes     │
│ seagate8tb-backups-photos-to-wd6tb            │ seagate8tb:/backups/photos       │ wd6tb:/backups/photos            │ src:btrfs, btrfs-snapshots(max:30)    │ yes     │
└───────────────────────────────────────────────┴──────────────────────────────────┴──────────────────────────────────┴───────────────────────────────────────┴─────────┘
```

### Links

- GitHub: https://github.com/iglootools/nbkp
- Usage: https://github.com/iglootools/nbkp/blob/main/docs/usage.md
- Demo: https://asciinema.org/a/2IZXgMdnSj6tNak8


### What do you think?
Happy to answer questions or hear how others handle similar setups. If you've been maintaining rsync scripts for a laptop + removable drives + home server workflow, I'd be curious to compare notes.

**Keep in mind that this is still alpha-quality software**, so expect bugs and rough edges. If you want to try it out, make sure to test with non-critical data first and report any issues you encounter!
