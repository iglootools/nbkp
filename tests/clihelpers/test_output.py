"""Tests for the shared JSON emission helper."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from nbkp.clihelpers import echo_json
from nbkp.fsprotocol import Snapshot


def _emit(data: object, capsys: pytest.CaptureFixture[str]) -> dict | list:
    """Call echo_json and parse what it printed."""
    echo_json(data)
    return json.loads(capsys.readouterr().out)


class TestEchoJson:
    def test_serializes_model_with_datetime_field(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A datetime field is the case a bare model_dump() gets wrong."""
        snapshot = Snapshot.create(datetime(2026, 3, 6, 14, 30, tzinfo=UTC))

        data = _emit(snapshot, capsys)

        assert isinstance(data, dict)
        assert data["name"] == "2026-03-06T14:30:00.000Z"
        assert isinstance(data["timestamp"], str)
        assert data["timestamp"].startswith("2026-03-06T14:30:00")

    def test_serializes_models_nested_in_containers(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Commands pass dicts and lists of models, not bare models."""
        snapshot = Snapshot.create(datetime(2026, 3, 6, 14, 30, tzinfo=UTC))

        data = _emit({"snapshots": [snapshot], "count": 1}, capsys)

        assert isinstance(data, dict)
        assert data["count"] == 1
        assert data["snapshots"][0]["name"] == "2026-03-06T14:30:00.000Z"

    def test_passes_through_plain_structures(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        data = _emit({"provider": "keyring", "ids": ["a", "b"]}, capsys)

        assert data == {"provider": "keyring", "ids": ["a", "b"]}

    def test_rejects_unsupported_types(self) -> None:
        """Unknown types still raise rather than being silently coerced."""
        with pytest.raises(TypeError, match="not JSON serializable"):
            echo_json({"path": object()})
