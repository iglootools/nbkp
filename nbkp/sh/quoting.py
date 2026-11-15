"""Shell quoting for generated scripts.

Paths baked into the script are literal, except for a few deliberate
runtime variables (``NBKP_SCRIPT_DIR``, ``NBKP_TS``, the ``snap`` loop
variable).  Those are embedded in Python strings as NUL-delimited
placeholders (see :func:`shell_var`): NUL can never occur in a path, so
a placeholder is never confused with a ``$`` that is part of a real
path.  :func:`quote` renders placeholders as ``${NAME}`` expansions and
quotes everything else literally.
"""

import re
import shlex
import textwrap

from ..remote import shell_join

_MARK = "\x00"
_DQ_SPECIAL = re.compile(r'([\\"$`])')

# One indentation level of generated shell code (also used for continuations).
INDENT = "    "


def shell_var(name: str) -> str:
    """Placeholder that :func:`quote` renders as the expansion ``${name}``."""
    return f"{_MARK}{name}{_MARK}"


SCRIPT_DIR = shell_var("NBKP_SCRIPT_DIR")
SNAPSHOT_TS = shell_var("NBKP_TS")
SNAP = shell_var("snap")


def has_shell_var(s: str) -> bool:
    return _MARK in s


def quote(s: str) -> str:
    """Quote *s* as one shell word, expanding only placeholders.

    Without placeholders, single quotes (``shlex.quote``).  With
    placeholders, a double-quoted word in which every literal character
    that is special inside double quotes (``\\ " $ ` ``) is escaped.
    """
    if not has_shell_var(s):
        return shlex.quote(s)
    else:
        parts = s.split(_MARK)  # odd indices are variable names
        rendered = (
            f"${{{part}}}" if i % 2 else _DQ_SPECIAL.sub(r"\\\1", part)
            for i, part in enumerate(parts)
        )
        return '"' + "".join(rendered) + '"'


def quote_args(args: list[str]) -> str:
    return " ".join(quote(a) for a in args)


def remote_command(args: list[str]) -> str:
    """Join *args* into one command string for the remote shell.

    Each argument is single-quoted for the remote side, except a leading
    ``~`` / ``~/``, which the remote shell must expand to the remote user's
    home (see :func:`nbkp.remote.ssh.shell_join`); placeholders stay
    inside those quotes and are expanded locally when the whole string is
    later passed through :func:`quote`.  Placeholder values must therefore
    be free of single quotes (timestamps and validated snapshot names).
    """
    return shell_join(args)


def format_command(cmd: list[str]) -> str:
    """Format a command with backslash continuations when it is long."""
    parts = [quote(arg) for arg in cmd]
    if len(parts) <= 3:
        return " ".join(parts)
    else:
        sep = f" \\\n{INDENT}"
        return parts[0] + sep + sep.join(parts[1:])


def comment_out(text: str) -> str:
    """Prefix every non-blank line with ``# ``."""
    return textwrap.indent(text, "# ")


def comment_text(s: str) -> str:
    """*s* made safe to embed in a ``#`` comment line.

    Non-printable characters are written as Python escapes (``\\n``): a
    newline would end the comment and run the rest of *s* as shell code.
    """
    return "".join(c if c.isprintable() else repr(c)[1:-1] for c in s)
