"""Stable error codes for the config validators nbkp owns.

Validators raise :func:`config_error` instead of a bare ``ValueError`` so that
each failure surfaces in ``ValidationError.errors()`` with a machine-readable
``type`` (one of :class:`ConfigValidationCode`). Callers and tests can then
branch on the code rather than match against the human-readable message.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic_core import PydanticCustomError


class ConfigValidationCode(StrEnum):
    """``type`` of the pydantic errors raised by nbkp's config validators."""

    UNKNOWN_REFERENCE = "unknown-reference"
    CIRCULAR_EXTENDS = "circular-extends"
    CIRCULAR_PROXY_JUMP = "circular-proxy-jump"
    MUTUALLY_EXCLUSIVE = "mutually-exclusive"
    DUPLICATE_ENDPOINT_LOCATION = "duplicate-endpoint-location"
    NESTED_ENDPOINT = "nested-endpoint"
    SHARED_DESTINATION = "shared-destination"
    CROSS_SERVER_SYNC = "cross-server-sync"
    CREDENTIAL_COMMAND_REQUIRED = "credential-command-required"
    CREDENTIAL_COMMAND_PLACEHOLDER = "credential-command-placeholder"
    PATH_REQUIRED = "path-required"
    INVALID_FILTER = "invalid-filter"
    FILTERS_NOT_A_LIST = "filters-not-a-list"
    UNKNOWN_DIR_MERGE_OPTION = "unknown-dir-merge-option"


def config_error(code: ConfigValidationCode, message: str) -> PydanticCustomError:
    """Build a pydantic error carrying a stable ``code`` and a display message.

    The message travels as a context value behind a fixed ``{message}``
    template, so braces in it (e.g. the literal ``{id}`` placeholder) are
    rendered verbatim rather than read as template fields.
    """
    return PydanticCustomError(code.value, "{message}", {"message": message})
