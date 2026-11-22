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
from . import mount_cmd as mount_cmd
from . import umount_cmd as umount_cmd
from . import status_cmd as status_cmd
from . import setup_auth_cmd as setup_auth_cmd

# isort: on
