FROM debian:latest

# udisks2 + udisks2-btrfs provide the production mount backend; dbus and
# policykit-1 provide the system bus and polkitd that udisksd authorizes
# against (no systemd in this image — the daemons are started directly by
# entrypoint.sh).  cryptsetup/btrfs-progs are still needed by setup-luks.sh
# to *create* the encrypted btrfs volume that udisks later unlocks/mounts.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    openssh-server \
    rsync \
    btrfs-progs \
    cryptsetup \
    sudo \
    e2fsprogs \
    udisks2 \
    udisks2-btrfs \
    polkitd \
    dbus \
    udev \
    util-linux \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -s /bin/bash testuser && \
    mkdir -p /home/testuser/.ssh && \
    chmod 700 /home/testuser/.ssh && \
    chown testuser:testuser /home/testuser/.ssh

RUN mkdir -p /run/sshd

COPY entrypoint.sh /entrypoint.sh
COPY setup-luks.sh /setup-luks.sh
RUN chmod +x /entrypoint.sh /setup-luks.sh && \
    printf '%s\n' \
      'testuser ALL=(root) NOPASSWD: /setup-luks.sh' \
      > /etc/sudoers.d/nbkp-setup && \
    chmod 440 /etc/sudoers.d/nbkp-setup

EXPOSE 22

ENTRYPOINT ["/entrypoint.sh"]
