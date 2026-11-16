"""Tests for `disks status`, against a host whose answers a FakeCommandRunner
scripts, through an injected runtime."""

import json
from pathlib import Path

from nbkp.cli import app
from nbkp.config import (
    Config,
    LocalVolume,
    MountConfig,
    RemoteVolume,
    SshEndpoint,
)
from nbkp.config.testkit import write_config
from tests.clidisplay import runner
from tests.cliwiring.test_mount import UUID, encrypted_volume
from tests.fakes import FakeCommandRunner, completed, fake_runtime

_PLAIN_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _mount_config_for_status() -> Config:
    """Config with volumes that have mount config."""
    return Config(
        volumes={
            "encrypted-drive": encrypted_volume("/mnt/encrypted").model_copy(
                update={"slug": "encrypted-drive"}
            ),
            "plain-drive": LocalVolume(
                slug="plain-drive",
                path="/mnt/plain",
                mount=MountConfig(device_uuid=_PLAIN_UUID),
            ),
            "no-mount": LocalVolume(slug="no-mount", path="/home/user"),
        },
    )


def _host() -> FakeCommandRunner:
    """``encrypted-drive`` unlocked but not mounted; ``plain-drive`` mounted."""
    return (
        FakeCommandRunner()
        .on("lsblk", returns=completed(stdout=f"sdb part\nluks-{UUID} crypt\n"))
        .on("findmnt", "--source", f"/dev/mapper/luks-{UUID}", returns=completed(1))
        .on(
            "findmnt",
            "--source",
            f"/dev/disk/by-uuid/{_PLAIN_UUID}",
            returns=completed(stdout="/mnt/plain\n"),
        )
    )


def _status(
    config_path: Path, *args: str, run: FakeCommandRunner | None = None
) -> tuple[int, str]:
    result = runner.invoke(
        app,
        ["disks", "status", "--config", str(config_path), *args],
        obj=fake_runtime(run=run if run is not None else _host()),
    )
    return result.exit_code, result.output


class TestVolumesStatusCommand:
    def test_json_output(self, tmp_path: Path) -> None:
        exit_code, output = _status(
            write_config(tmp_path, _mount_config_for_status()), "-o", "json"
        )

        assert exit_code == 0
        data = json.loads(output)
        assert len(data) == 3
        assert data[0]["volume"] == "encrypted-drive"
        assert data[0]["device_present"] is True
        assert data[0]["luks_unlocked"] is True
        assert data[0]["mounted"] is False
        assert data[1]["volume"] == "plain-drive"
        assert data[1]["mounted"] is True
        # Unmanaged volumes are folded into the Name column with a
        # "(not managed)" marker and carry all-None mount state.
        assert "no-mount" in data[2]["volume"]
        assert "not managed" in data[2]["volume"]
        assert data[2]["device_present"] is None
        assert data[2]["mounted"] is None

    def test_human_output(self, tmp_path: Path) -> None:
        exit_code, output = _status(write_config(tmp_path, _mount_config_for_status()))

        assert exit_code == 0
        assert "Volume Mount Status" in output

    def test_unreachable_volume(self, tmp_path: Path) -> None:
        unreachable = FakeCommandRunner().on("test", returns=OSError("timed out"))

        exit_code, output = _status(
            write_config(tmp_path, _mount_config_for_status()),
            "-o",
            "json",
            "--name",
            "plain-drive",
            run=unreachable,
        )

        assert exit_code == 0
        [entry] = json.loads(output)
        assert "unreachable: timed out" in entry["volume"]
        assert entry["mounted"] is None

    def test_name_filter(self, tmp_path: Path) -> None:
        run = _host()

        exit_code, output = _status(
            write_config(tmp_path, _mount_config_for_status()),
            "-o",
            "json",
            "--name",
            "encrypted-drive",
            run=run,
        )

        assert exit_code == 0
        data = json.loads(output)
        assert [entry["volume"] for entry in data] == ["encrypted-drive"]
        assert not any(_PLAIN_UUID in " ".join(cmd) for cmd in run.commands)

    def test_no_mount_config(self, tmp_path: Path) -> None:
        config = Config(volumes={"plain": LocalVolume(slug="plain", path="/home/user")})

        exit_code, output = _status(write_config(tmp_path, config), "-o", "json")

        assert exit_code == 0
        data = json.loads(output)
        assert len(data) == 1
        assert "plain" in data[0]["volume"]
        assert "not managed" in data[0]["volume"]

    def test_location_excluded_volume_not_probed(self, tmp_path: Path) -> None:
        config_path = write_config(
            tmp_path,
            Config(
                ssh_endpoints={
                    "nas": SshEndpoint(slug="nas", host="nas.lan", location="home"),
                },
                volumes={
                    "nas-drive": RemoteVolume(
                        slug="nas-drive",
                        ssh_endpoint="nas",
                        path="/mnt/nas-drive",
                        mount=MountConfig(device_uuid=_PLAIN_UUID),
                    ),
                },
            ),
        )
        run = _host()

        exit_code, output = _status(config_path, "-o", "json", "-L", "home", run=run)

        assert exit_code == 0
        assert run.calls == []
        data = json.loads(output)
        assert len(data) == 1
        assert "location excluded" in data[0]["volume"]
        assert data[0]["mounted"] is None
