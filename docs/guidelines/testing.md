# Testing Conventions

- **Testing**
  - No real rsync/ssh/btrfs calls in unit tests: they run against fakes, and the
    Docker-enabled integration tests cover the real interactions.
  - nbkp's building blocks for testing without patching:
    - **Deciders**: the preflight `*Status.from_diagnostics` interpretation, and
      the CLI deciders over `PreflightResult` / `SyncResult` / mount results.
    - **Fakes**, in `tests/fakes.py`: `FakeCommandRunner` for any
      `run: VolumeCommandRunner`, `FakeRsync`, and for CLI tests a fake
      `Runtime` passed as `CliRunner.invoke(..., obj=fake_runtime(...))`.
      Smaller seams follow the same shape: `environ`, `which`, `run_process`,
      `has_docker`. `completed()` builds a real `CompletedProcess`.
    - **Real local resources**: a Python child process, a file under `tmp_path`,
      the in-process SSH server of `tests/remote/test_host_key_verification.py`.
    - **`chdir`** (`tests/conftest.py`): changes the working directory until the
      end of the test, so relative config paths are predictable.

- Ensure that all new functionality is covered by tests according to the
  [testing strategy](../building-and-testing.md#testing-strategy).
