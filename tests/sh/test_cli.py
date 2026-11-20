import stat
from collections.abc import Callable
from pathlib import Path

from nbkp.cli import app
from nbkp.config.testkit import write_config
from tests.clidisplay import (
    runner,
    sample_config,
)
from tests.sh.test_sh import mount_managed_config


class TestShCommand:
    def test_generates_script(self, tmp_path: Path) -> None:
        config_path = write_config(tmp_path, sample_config())

        result = runner.invoke(app, ["sh", "--config", str(config_path)])
        assert result.exit_code == 0
        assert "#!/bin/bash" in result.output
        assert "set -euo pipefail" in result.output
        assert "sync_photos_to_nas()" in result.output

    def test_config_path_in_header(self, tmp_path: Path) -> None:
        config_path = write_config(tmp_path, sample_config())

        result = runner.invoke(app, ["sh", "--config", str(config_path)])
        assert result.exit_code == 0
        assert f"# Config: {config_path}" in result.output

    def test_output_file(self, tmp_path: Path) -> None:
        config_path = write_config(tmp_path, sample_config())
        out = tmp_path / "backup.sh"

        result = runner.invoke(
            app,
            ["sh", "--config", str(config_path), "-o", str(out)],
        )
        assert result.exit_code == 0
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "#!/bin/bash" in content
        assert "sync_photos_to_nas()" in content
        mode = out.stat().st_mode
        assert mode & stat.S_IXUSR
        assert mode & stat.S_IXGRP

    def test_output_file_reported_relative_to_cwd(
        self,
        tmp_path: Path,
        chdir: Callable[[Path], None],
    ) -> None:
        chdir(tmp_path)
        (tmp_path / "out").mkdir()
        write_config(tmp_path, sample_config())

        result = runner.invoke(
            app, ["sh", "--config", "config.yaml", "-o", "out/backup.sh"]
        )
        assert result.exit_code == 0
        assert "Written to out/backup.sh" in result.output

    def test_relative_without_output_file(self) -> None:
        result = runner.invoke(
            app,
            ["sh", "--config", "/fake.yaml", "--relative-src"],
        )
        assert result.exit_code == 2

    def test_relative_with_output_file(self, tmp_path: Path) -> None:
        config_path = write_config(tmp_path, sample_config())
        out = tmp_path / "backup.sh"

        result = runner.invoke(
            app,
            [
                "sh",
                "--config",
                str(config_path),
                "-o",
                str(out),
                "--relative-src",
            ],
        )
        assert result.exit_code == 0
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "NBKP_SCRIPT_DIR" in content

    def test_config_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "config.yaml"
        bad.write_text("volumes:\n  v:\n    type: ftp\n    path: /x\n")

        result = runner.invoke(app, ["sh", "--config", str(bad)])
        assert result.exit_code == 2

    def test_volume_without_path_is_an_error_not_a_traceback(
        self, tmp_path: Path
    ) -> None:
        config_path = write_config(tmp_path, mount_managed_config())

        result = runner.invoke(app, ["sh", "--config", str(config_path)])

        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit)
        assert "volume 'dst' has no 'path'" in result.output
