import os
import re
import shlex
from functools import reduce

from ..config import LocalVolume, RemoteVolume, SshConnectionOptions, SshEndpoint
lazy from .endpoints import ResolvedEndpoints


def format_known_hosts_option(known_hosts_file: str) -> str:
    """``UserKnownHostsFile=<path>``, the value double-quoted when it holds
    whitespace or a quote: OpenSSH splits the value on whitespace into several
    files unless it is quoted, and unescapes ``\\`` and ``\"`` inside quotes.
    """
    if re.search(r'[\s"\\]', known_hosts_file) is None:
        return f"UserKnownHostsFile={known_hosts_file}"
    else:
        escaped = re.sub(r'(["\\])', r"\\\1", known_hosts_file)
        return f'UserKnownHostsFile="{escaped}"'


def _ssh_o_options(opts: SshConnectionOptions) -> list[str]:
    return [
        opt
        for opt in [
            f"ConnectTimeout={opts.connect_timeout}",
            "BatchMode=yes",
            "Compression=yes" if opts.compress else None,
            (
                f"ServerAliveInterval={opts.server_alive_interval}"
                if opts.server_alive_interval is not None
                else None
            ),
            "StrictHostKeyChecking=no" if not opts.strict_host_key_checking else None,
            (
                format_known_hosts_option(opts.known_hosts_file)
                if opts.known_hosts_file is not None
                else None
            ),
            # Suppress the "Permanently added ... to the list of known hosts"
            # warning ssh would print on every connection when host-key
            # verification is fully disabled. With a real file it is printed
            # once per new host, and other warnings stay worth seeing.
            (
                "LogLevel=ERROR"
                if not opts.strict_host_key_checking
                and opts.known_hosts_file == os.devnull
                else None
            ),
            "ForwardAgent=yes" if opts.forward_agent else None,
        ]
        if opt is not None
    ]


def _port_and_key_args(endpoint: SshEndpoint) -> list[str]:
    return [
        *(["-p", str(endpoint.port)] if endpoint.port != 22 else []),
        *(["-i", endpoint.key] if endpoint.key else []),
    ]


def _ssh_endpoint_args(endpoint: SshEndpoint) -> list[str]:
    o_args = [
        arg
        for opt in _ssh_o_options(endpoint.connection_options)
        for arg in ["-o", opt]
    ]
    return [*o_args, *_port_and_key_args(endpoint)]


def _build_proxy_hop(proxy: SshEndpoint, inner_cmd: str | None) -> str:
    """ssh runs a ProxyCommand through ``sh -c``, so the hop is shell-quoted
    (``shlex.join``). Only ``%`` needs ssh-level escaping in a nested
    command: ``%h:%p`` itself contains no shell metacharacters and stays
    unquoted for ssh to expand.
    """
    parts = [
        "ssh",
        *_ssh_endpoint_args(proxy),
        *(["-o", f"ProxyCommand={inner_cmd.replace('%', '%%')}"] if inner_cmd else []),
        "-W",
        "%h:%p",
        proxy.destination,
    ]
    return shlex.join(parts)


def _build_proxy_command(
    proxies: list[SshEndpoint],
) -> str:
    """Uses ProxyCommand instead of -J so that per-proxy SSH
    options (e.g. StrictHostKeyChecking) are propagated to
    each hop. *proxies* must not be empty.
    """
    innermost, *outer = proxies
    return reduce(
        lambda cmd, proxy: _build_proxy_hop(proxy, cmd),
        outer,
        _build_proxy_hop(innermost, None),
    )


def _build_ssh_core_args(
    server: SshEndpoint,
    proxy_chain: list[SshEndpoint] | None = None,
) -> list[str]:
    return [
        *_ssh_endpoint_args(server),
        *(
            ["-o", f"ProxyCommand={_build_proxy_command(proxy_chain)}"]
            if proxy_chain
            else []
        ),
    ]


def build_ssh_base_args(
    server: SshEndpoint,
    proxy_chain: list[SshEndpoint] | None = None,
) -> list[str]:
    """Returns args like ``ssh -o ConnectTimeout=10 -o BatchMode=yes [opts] host``."""
    return ["ssh", *_build_ssh_core_args(server, proxy_chain), server.destination]


def build_ssh_e_option(
    server: SshEndpoint,
    proxy_chain: list[SshEndpoint] | None = None,
) -> list[str]:
    """Returns a list like:

        ["-e", "ssh -o ConnectTimeout=10 -o BatchMode=yes ..."]

    rsync splits the ``-e`` value on spaces, honoring single and double
    quotes (but not backslashes), so every argument is POSIX-quoted with
    ``shlex.join`` — which never emits backslashes. This covers a key path
    containing spaces as well as the ProxyCommand value.
    """
    return ["-e", shlex.join(["ssh", *_build_ssh_core_args(server, proxy_chain)])]


def format_remote_path(server: SshEndpoint, path: str) -> str:
    return f"{server.destination}:{path}"


# Below: commands printed for the operator to copy-paste (troubleshooting
# output), not run by nbkp itself.


def format_proxy_jump_chain(proxies: list[SshEndpoint]) -> str:
    return ",".join(p.host_label for p in proxies)


def ssh_prefix(
    server: SshEndpoint,
    proxy_chain: list[SshEndpoint] | None = None,
) -> list[str]:
    return [
        "ssh",
        *_port_and_key_args(server),
        *(["-J", format_proxy_jump_chain(proxy_chain)] if proxy_chain else []),
        server.destination,
    ]


def _quote_arg(arg: str) -> str:
    """Shell-quote one argument, keeping a leading ``~`` / ``~/`` expandable.

    Remote volume paths may start with ``~`` (the remote user's home); quoting
    the tilde would stop the remote shell from expanding it.
    """
    match arg.partition("/"):
        case ("~", sep, rest):
            return f"~{sep}{shlex.quote(rest)}" if rest else f"~{sep}"
        case _:
            return shlex.quote(arg)


def shell_join(cmd: list[str]) -> str:
    """Join arguments into a shell command line (``shlex.join``, tilde-aware)."""
    return " ".join(_quote_arg(arg) for arg in cmd)


def wrap_cmd(
    cmd: list[str],
    vol: LocalVolume | RemoteVolume,
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    """Render a command as a copy-pasteable shell line, over SSH if remote.

    For a remote volume the quoted command becomes a single argument to
    ``ssh``, which hands it to the remote shell to parse again.
    """
    match vol:
        case LocalVolume():
            return shell_join(cmd)
        case RemoteVolume():
            ep = resolved_endpoints[vol.slug]
            return shlex.join([*ssh_prefix(ep.server, ep.proxy_chain), shell_join(cmd)])
