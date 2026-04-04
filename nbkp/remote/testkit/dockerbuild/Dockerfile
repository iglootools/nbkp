FROM debian:latest

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    openssh-server \
    rsync \
    btrfs-progs \
    cryptsetup \
    sudo \
    e2fsprogs \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -s /bin/bash testuser && \
    mkdir -p /home/testuser/.ssh && \
    chmod 700 /home/testuser/.ssh && \
    chown testuser:testuser /home/testuser/.ssh

RUN mkdir -p /run/sshd

COPY entrypoint.sh /entrypoint.sh
COPY setup-luks.sh /setup-luks.sh
RUN chmod +x /entrypoint.sh /setup-luks.sh && \
    echo "testuser ALL=(root) NOPASSWD: /setup-luks.sh" > /etc/sudoers.d/nbkp-setup && \
    chmod 440 /etc/sudoers.d/nbkp-setup

EXPOSE 22

ENTRYPOINT ["/entrypoint.sh"]
