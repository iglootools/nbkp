"""Disks CLI sub-app."""

import typer

app = typer.Typer(
    name="disks",
    help="Disk mount management commands",
    no_args_is_help=True,
)

from . import mount_cmd as _mount_cmd  # noqa: E402, F401
from . import umount_cmd as _umount_cmd  # noqa: E402, F401
from . import status_cmd as _status_cmd  # noqa: E402, F401
from . import setup_auth_cmd as _setup_auth_cmd  # noqa: E402, F401
