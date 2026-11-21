# Testing Conventions

What nbkp's tests must respect, and the building blocks they test with. Read it
before changing `tests/` or any `nbkp/*/testkit/`. The shared rules are
[general.md › Testability](https://github.com/iglootools/common/blob/main/guidelines/coding/general.md#testability)
and
[python.md › Tests](https://github.com/iglootools/common/blob/main/guidelines/coding/python.md#tests),
among them
[Build serialized test data from the models](https://github.com/iglootools/common/blob/main/guidelines/coding/python.md#build-serialized-test-data-from-the-models)
and choosing a backend such as the keyring's through the library's own API
(`keyring.set_keyring`). The patches nbkp allows are listed in
[docs/guidelines.md › Testing](../guidelines.md#testing); the test suites and
the testing strategy are in
[building-and-testing.md](../building-and-testing.md#testing-strategy).

## Rules

### Keep real rsync, ssh and btrfs out of the unit tests

Unit tests run every external call against fakes; the Docker-enabled
integration tests cover the real interactions.

**Why:** the unit suite then runs anywhere, with no external dependencies, and
fast enough for every commit.

### Cover new functionality as the testing strategy describes

Each new feature gets tests in the suites the
[testing strategy](../building-and-testing.md#testing-strategy) assigns to what
it touches.

**Why:** the suites split what they need by infrastructure, so a feature tested
only in the wrong one is left uncovered where it can break.

## Building blocks

Test without patching, with the first of these that fits:

- **Deciders**: the preflight `*Status.from_diagnostics` interpretation, and the
  CLI deciders over `PreflightResult` / `SyncResult` / mount results.
- **Fakes**, in `tests/fakes.py`: `FakeCommandRunner` for any
  `run: VolumeCommandRunner`, `FakeRsync`, and for CLI tests a fake `Runtime`
  passed as `CliRunner.invoke(..., obj=fake_runtime(...))`. Smaller seams follow
  the same shape: `environ`, `which`, `run_process`, `has_docker`. `completed()`
  builds a real `CompletedProcess`.
- **Real local resources**: a Python child process, a file under `tmp_path`, the
  in-process SSH server of `tests/remote/test_host_key_verification.py`.
- **`chdir`** (`tests/conftest.py`): changes the working directory until the end
  of the test, so relative config paths are predictable.
