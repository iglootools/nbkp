"""Disks CLI sub-app."""

import typer

app = typer.Typer(
    name="disks",
    help="Disk mount management commands",
    no_args_is_help=True,
)

# Import order determines the order commands are listed in `--help` and in the
# generated CLI reference, so it follows the disk lifecycle rather than the
# alphabet. Keep isort out of it.
# isort: off
from . import mount_cmd as _mount_cmd  # noqa: F401
from . import umount_cmd as _umount_cmd  # noqa: F401
from . import status_cmd as _status_cmd  # noqa: F401
from . import setup_auth_cmd as _setup_auth_cmd  # noqa: F401

# isort: on
