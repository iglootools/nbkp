"""
Integration tests: remote rsync paths reach the remote rsync as written (Docker).

rsync runs with ``--protect-args``, so the remote shell neither splits nor expands
a remote path, and a leading ``~`` is sent relative to the SSH user's home.
"""

from pathlib import Path

from nbkp.config import (
    Config,
    LocalVolume,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.remote.resolution import resolve_all_endpoints
from nbkp.remote.testkit.docker import REMOTE_BACKUP_PATH
from nbkp.sync.rsync import run_rsync
from tests._docker_fixtures import ssh_exec, ssh_run

# A space, a semicolon and a `$`: each one the remote shell would act on.
_AWKWARD = "my photos; $HOME"


def _config(
    src: LocalVolume | RemoteVolume,
    dst: LocalVolume | RemoteVolume,
    server: SshEndpoint,
    *,
    src_subdir: str | None = None,
    dst_subdir: str | None = None,
) -> tuple[SyncConfig, Config]:
    sync = SyncConfig(slug="test-sync", source="ep-src", destination="ep-dst")
    config = Config(
        ssh_endpoints={"test-server": server},
        volumes={"src": src, "dst": dst},
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src", subdir=src_subdir),
            "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst", subdir=dst_subdir),
        },
        syncs={"test-sync": sync},
    )
    return sync, config


def _local_source(tmp_path: Path) -> LocalVolume:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "img.jpg").write_text("image-data")
    return LocalVolume(slug="src", path=str(src_dir))


class TestRemotePaths:
    def test_local_to_remote_path_with_shell_characters(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        docker_remote_volume: RemoteVolume,
    ) -> None:
        subdir = f"{tmp_path.name} {_AWKWARD}"
        sync, config = _config(
            _local_source(tmp_path),
            docker_remote_volume.model_copy(update={"slug": "dst"}),
            docker_ssh_endpoint,
            dst_subdir=subdir,
        )

        result = run_rsync(
            sync,
            config,
            resolved_endpoints=resolve_all_endpoints(config),
        )

        assert result.returncode == 0, result.stdout
        check = ssh_run(
            docker_ssh_endpoint,
            ["cat", f"{REMOTE_BACKUP_PATH}/{subdir}/img.jpg"],
        )
        assert check.stdout.strip() == "image-data"

    def test_remote_to_local_path_with_shell_characters(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
        docker_remote_volume: RemoteVolume,
    ) -> None:
        subdir = f"{tmp_path.name} {_AWKWARD}"
        remote_dir = f"{REMOTE_BACKUP_PATH}/{subdir}"
        ssh_run(docker_ssh_endpoint, ["mkdir", "-p", remote_dir])
        ssh_run(
            docker_ssh_endpoint,
            ["sh", "-c", 'echo image-data > "$1/img.jpg"', "sh", remote_dir],
        )
        dst_dir = tmp_path / "dst"
        dst_dir.mkdir()
        sync, config = _config(
            docker_remote_volume.model_copy(update={"slug": "src"}),
            LocalVolume(slug="dst", path=str(dst_dir)),
            docker_ssh_endpoint,
            src_subdir=subdir,
        )

        result = run_rsync(
            sync,
            config,
            resolved_endpoints=resolve_all_endpoints(config),
        )

        assert result.returncode == 0, result.stdout
        assert (dst_dir / "img.jpg").read_text().strip() == "image-data"

    def test_local_to_remote_tilde_path(
        self,
        tmp_path: Path,
        docker_ssh_endpoint: SshEndpoint,
    ) -> None:
        """No shell expands ``~`` under ``--protect-args``: nbkp makes it relative."""
        subdir = f"{tmp_path.name} tilde"
        sync, config = _config(
            _local_source(tmp_path),
            RemoteVolume(slug="dst", ssh_endpoint="test-server", path="~"),
            docker_ssh_endpoint,
            dst_subdir=subdir,
        )

        result = run_rsync(
            sync,
            config,
            resolved_endpoints=resolve_all_endpoints(config),
        )

        assert result.returncode == 0, result.stdout
        check = ssh_exec(docker_ssh_endpoint, f'cat "$HOME/{subdir}/img.jpg"')
        assert check.stdout.strip() == "image-data"
