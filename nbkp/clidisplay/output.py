"""The ``--output`` formats, and printing a command's result as JSON."""

import enum
import json

import typer
from pydantic import BaseModel


class OutputFormat(enum.StrEnum):
    """How a command prints its result: Rich tables for a human, or JSON."""

    HUMAN = "human"
    JSON = "json"


class NotJsonSerializableError(TypeError):
    """
    A value has no JSON form.

    A ``TypeError``, as :mod:`json` expects from a ``default`` encoder.
    """

    def __init__(self, value: object) -> None:
        """Build the error from the value; it keeps only the value's type name."""
        self.type_name = type(value).__name__
        super().__init__(f"Object of type {self.type_name} is not JSON serializable")


def _as_json_value(value: object) -> object:
    """
    Fallback encoder for values :mod:`json` cannot serialize on its own.

    Only Pydantic models are handled, and deliberately via ``mode="json"``:
    that is what turns ``datetime`` — and every other field whose Python type
    has no JSON counterpart — into a JSON-native value.  A bare
    ``model_dump()`` leaves them as Python objects for ``json.dumps`` to
    choke on.
    """
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    raise NotJsonSerializableError(value)


def echo_json(data: object) -> None:
    """
    Print *data* as indented JSON, serializing any Pydantic models within.

    Commands pass their models through as-is instead of dumping them at the
    call site, so the ``mode="json"`` decision above is made in exactly one
    place.  Dumping per call site is what let ``datetime`` fields through
    unserialized: the failure only surfaces once an optional field like a
    ``latest`` symlink's snapshot is actually populated, so a fixture that
    leaves it unset reports success either way.
    """
    typer.echo(json.dumps(data, indent=2, default=_as_json_value))
