"""The ``nbkp disks`` command group: mount, umount, status and setup-auth."""

import typer

from nbkp.disks.cli.mount_cmd import mount_cmd
from nbkp.disks.cli.setup_auth_cmd import setup_auth_cmd
from nbkp.disks.cli.status_cmd import status_cmd
from nbkp.disks.cli.umount_cmd import umount_cmd

app = typer.Typer(
    name="disks",
    help="Disk mount management commands",
    no_args_is_help=True,
)

# Registration order is the order commands are listed in `--help` and in the
# generated CLI reference, so it follows the disk lifecycle rather than the
# alphabet.
app.command("mount")(mount_cmd)
app.command("umount")(umount_cmd)
app.command("status")(status_cmd)
app.command("setup-auth")(setup_auth_cmd)
