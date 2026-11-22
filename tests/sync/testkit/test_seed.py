import subprocess
from pathlib import Path

from nbkp.config import (
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.fsprotocol import DESTINATION_SENTINEL, LATEST_LINK, VOLUME_SENTINEL
from nbkp.sync.testkit.seed import create_seed_sentinels, seed_volume


def _config(local: Path, remote: Path) -> Config:
    return Config(
        ssh_endpoints={"host": SshEndpoint(slug="host", host="host")},
        volumes={
            "src": LocalVolume(slug="src", path=str(local)),
            "dst": RemoteVolume(slug="dst", ssh_endpoint="host", path=str(remote)),
        },
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-dst": SyncEndpoint(
                slug="ep-dst",
                volume="dst",
                subdir="sub dir",
                hard_link_snapshots=HardLinkSnapshotConfig(enabled=True),
            ),
        },
        syncs={"s": SyncConfig(slug="s", source="ep-src", destination="ep-dst")},
    )


def _run_here(_vol: object, command: list[str]) -> None:
    subprocess.run(command, check=True)


class TestRemoteSeeding:
    """
    The "remote" commands run locally, each path kept as one argument.

    Even a path with spaces and shell metacharacters.
    """

    def test_remote_sentinels_land_at_paths_with_shell_metacharacters(
        self,
        tmp_path: Path,
    ) -> None:
        remote = tmp_path / "my backups; touch pwned $(id)"
        create_seed_sentinels(_config(tmp_path / "src", remote), remote_exec=_run_here)

        assert (remote / VOLUME_SENTINEL).is_file()
        assert (remote / "sub dir" / DESTINATION_SENTINEL).is_file()
        assert (remote / "sub dir" / LATEST_LINK).is_symlink()
        assert sorted(p.name for p in tmp_path.iterdir()) == [remote.name, "src"]

    def test_remote_latest_link_is_kept_when_it_exists(self, tmp_path: Path) -> None:
        remote = tmp_path / "dst"
        latest = remote / "sub dir" / LATEST_LINK
        (latest.parent / "snapshots" / "1").mkdir(parents=True)
        latest.symlink_to("snapshots/1")

        create_seed_sentinels(_config(tmp_path / "src", remote), remote_exec=_run_here)

        assert latest.readlink() == Path("snapshots/1")

    def test_remote_sample_files_keep_their_content(self, tmp_path: Path) -> None:
        remote = tmp_path / "a b"
        volume = RemoteVolume(slug="dst", ssh_endpoint="host", path=str(remote))

        seed_volume(
            volume,
            big_file_size_bytes=3,
            remote_exec=lambda command: _run_here(None, command),
        )

        assert (remote / "sample.txt").read_text() == "Sample data for backup testing\n"
        assert (remote / "excluded" / "debug.log").is_file()
        assert (remote / "large-file.bin").stat().st_size == 3
