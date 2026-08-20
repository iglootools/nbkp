"""Output format enum and JSON emission shared across CLI commands."""

from __future__ import annotations

import enum
import json
from typing import Any

import typer
from pydantic import BaseModel


class OutputFormat(str, enum.Enum):
    """Output format for CLI commands."""

    HUMAN = "human"
    JSON = "json"


def _as_json_value(value: Any) -> Any:
    """Fallback encoder for values :mod:`json` cannot serialize on its own.

    Only Pydantic models are handled, and deliberately via ``mode="json"``:
    that is what turns ``datetime`` — and every other field whose Python type
    has no JSON counterpart — into a JSON-native value.  A bare
    ``model_dump()`` leaves them as Python objects for ``json.dumps`` to
    choke on.
    """
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def echo_json(data: Any) -> None:
    """Print *data* as indented JSON, serializing any Pydantic models within.

    Commands pass their models through as-is instead of dumping them at the
    call site, so the ``mode="json"`` decision above is made in exactly one
    place.  Dumping per call site is what let ``datetime`` fields through
    unserialized: the failure only surfaces once an optional field like a
    ``latest`` symlink's snapshot is actually populated, so a fixture that
    leaves it unset reports success either way.
    """
    typer.echo(json.dumps(data, indent=2, default=_as_json_value))
