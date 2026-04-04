"""Host-interaction primitives — re-exports from remote.queries.

This module re-exports all symbols from ``remote.queries`` for
backward compatibility.  New code should import directly from
``remote.queries``.
"""

from ..remote.queries import (  # noqa: F401
    _check_command_available as _check_command_available,
    _check_directory_writable as _check_directory_writable,
    _check_endpoint_sentinel as _check_endpoint_sentinel,
    _check_file_exists as _check_file_exists,
    _check_rsync_version as _check_rsync_version,
    _check_symlink_exists as _check_symlink_exists,
    _check_systemctl_cat as _check_systemctl_cat,
    _run_systemctl_show as _run_systemctl_show,
    check_directory_exists as check_directory_exists,
    parse_rsync_version as parse_rsync_version,
    read_symlink_target as read_symlink_target,
    resolve_endpoint as resolve_endpoint,
)
