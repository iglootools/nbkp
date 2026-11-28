"""The config models of an SSH endpoint and its connection options."""

from pathlib import Path

from pydantic import Field, field_validator, model_validator

from nbkp.config.protocol.base import ConfigModel, Slug
from nbkp.config.protocol.errors import ConfigValidationErrorKind, config_error

DEFAULT_SSH_PORT = 22

LOCAL_HOST_SLUG = "localhost"
"""
Slug under which preflight reports the machine nbkp runs on.

Local volumes run their tools there, so preflight keys its status next to the
configured endpoints' statuses; an endpoint may not take the slug, or its
volumes and the local ones would be checked as one host.
"""


def _expand_user(v: object) -> object:
    """
    ``~``-expand a path given as a string.

    Non-strings pass through so pydantic rejects them rather than silently
    dropping the value.
    """
    match v:
        case str():
            return str(Path(v).expanduser())
        case _:
            return v


class SshConnectionOptions(ConfigModel):
    """
    SSH connection options, applied across three layers.

    These fields map to parameters across three layers:
    - SSH client: ssh(1) -o options
    - Paramiko: SSHClient.connect() kwargs
      https://docs.paramiko.org/en/stable/api/client.html
    - Fabric: Connection() constructor
      https://docs.fabfile.org/en/stable/api/connection.html
    """

    # Maps to ssh's ConnectTimeout, Paramiko's timeout and Fabric's connect_timeout
    connect_timeout: int = Field(
        default=10,
        ge=1,
        description="Connection timeout in seconds",
    )
    # Maps to ssh's Compression and Paramiko's compress
    compress: bool = Field(default=False, description="Enable SSH compression")
    # Maps to ssh's ServerAliveInterval and Paramiko's transport.set_keepalive()
    server_alive_interval: int | None = Field(
        default=None,
        ge=1,
        description="Keepalive interval in seconds",
    )

    # Maps to Paramiko's allow_agent
    allow_agent: bool = Field(
        default=True,
        description="Use SSH agent for authentication",
    )
    # Maps to Paramiko's look_for_keys
    look_for_keys: bool = Field(default=True, description="Search ~/.ssh/ for keys")

    # Maps to Paramiko's banner_timeout
    banner_timeout: float | None = Field(
        default=None,
        ge=0,
        description="Wait time for SSH banner",
    )
    # Maps to Paramiko's auth_timeout
    auth_timeout: float | None = Field(
        default=None,
        ge=0,
        description="Wait time for auth response",
    )
    # Maps to Paramiko's channel_timeout (no SSH CLI equivalent)
    channel_timeout: float | None = Field(
        default=None,
        ge=0,
        description="Wait time for channel open (Paramiko/Fabric only)",
    )

    # Maps to ssh's StrictHostKeyChecking and the policy set by Paramiko's
    # SSHClient.set_missing_host_key_policy() method
    strict_host_key_checking: bool = Field(
        default=True,
        description="Verify remote host key",
    )
    # Maps to ssh's UserKnownHostsFile and Paramiko's SSHClient.load_host_keys()
    known_hosts_file: str | None = Field(
        default=None,
        description="Custom known hosts file path",
    )

    @field_validator("known_hosts_file", mode="before")
    @classmethod
    def normalize_known_hosts_file(cls, v: object) -> object:
        """Return the known-hosts path with ``~`` expanded."""
        # OpenSSH expands ``~`` in UserKnownHostsFile, Paramiko's
        # load_host_keys does not: expand it once here for both.
        return _expand_user(v)

    # Maps to ssh's ForwardAgent and Fabric's forward_agent
    forward_agent: bool = Field(
        default=False,
        description="Enable SSH agent forwarding",
    )

    # Maps to Paramiko's disabled_algorithms (no SSH CLI equivalent)
    disabled_algorithms: dict[str, list[str]] | None = Field(
        default=None,
        description="Disable specific SSH algorithms (Paramiko/Fabric only)",
    )


class SshEndpoint(ConfigModel):
    """
    A host nbkp reaches over SSH, and how to reach it.

    It may jump through other endpoints (``proxy-jump`` or ``proxy-jumps``),
    carry location tags that pick it on a given network, and ``extends``
    another endpoint to inherit its fields.
    """

    slug: Slug
    host: str = Field(..., min_length=1, description="Hostname or SSH config alias")
    port: int = Field(default=DEFAULT_SSH_PORT, ge=1, le=65535, description="SSH port")
    user: str | None = Field(default=None, description="SSH username")
    key: str | None = Field(default=None, description="Path to private key file")

    @field_validator("slug")
    @classmethod
    def validate_slug_not_reserved(cls, v: str) -> str:
        """Raise a ``reserved-slug`` error for the slug of the local host."""
        if v == LOCAL_HOST_SLUG:
            raise config_error(
                ConfigValidationErrorKind.RESERVED_SLUG,
                f"SSH endpoint slug '{LOCAL_HOST_SLUG}' is reserved for the machine"
                " nbkp runs on: rename the endpoint (its host may stay"
                f" '{LOCAL_HOST_SLUG}')",
            )
        return v

    @field_validator("key", mode="before")
    @classmethod
    def normalize_key(cls, v: object) -> object:
        """Return the private key path with ``~`` expanded."""
        return _expand_user(v)

    connection_options: SshConnectionOptions = Field(
        default_factory=SshConnectionOptions,
        description="SSH connection options",
    )
    proxy_jump: str | None = Field(
        default=None,
        description="Slug of another endpoint to use as bastion",
    )
    proxy_jumps: list[str] | None = Field(
        default=None,
        description=(
            "Slugs for multi-hop proxy chain (mutually exclusive with `proxy-jump`)"
        ),
    )
    location: str | None = Field(
        default=None,
        description="Network location tag (e.g. `home`, `travel`)",
    )
    locations: list[str] | None = Field(
        default=None,
        description=("Multiple location tags (mutually exclusive with `location`)"),
    )
    extends: str | None = Field(
        default=None,
        description="Slug of parent endpoint to inherit from",
    )

    @model_validator(mode="after")
    def validate_proxy_exclusivity(self) -> SshEndpoint:
        """Raise a ``mutually-exclusive`` error if both proxy-jump fields are set."""
        if self.proxy_jump is not None and self.proxy_jumps is not None:
            raise config_error(
                ConfigValidationErrorKind.MUTUALLY_EXCLUSIVE,
                "proxy-jump and proxy-jumps are mutually exclusive",
            )
        return self

    @model_validator(mode="after")
    def validate_location_exclusivity(self) -> SshEndpoint:
        """Raise a ``mutually-exclusive`` error if both location fields are set."""
        if self.location is not None and self.locations is not None:
            raise config_error(
                ConfigValidationErrorKind.MUTUALLY_EXCLUSIVE,
                "location and locations are mutually exclusive",
            )
        return self

    @property
    def proxy_jump_chain(self) -> list[str]:
        """Return the slugs of the bastions to jump through, in order; empty if none."""
        if self.proxy_jumps is not None:
            return list(self.proxy_jumps)
        elif self.proxy_jump is not None:
            return [self.proxy_jump]
        else:
            return []

    @property
    def location_list(self) -> list[str]:
        """Return the endpoint's location tags, from either field; empty if none."""
        if self.locations is not None:
            return list(self.locations)
        elif self.location is not None:
            return [self.location]
        else:
            return []

    @property
    def destination(self) -> str:
        """``user@host``, or ``host`` when no user is set: ssh's destination."""
        return f"{self.user}@{self.host}" if self.user else self.host

    @property
    def host_label(self) -> str:
        """The destination with its port when not 22 (``user@host:2222``)."""
        return (
            f"{self.destination}:{self.port}"
            if self.port != DEFAULT_SSH_PORT
            else self.destination
        )
