"""E2E sync tests — Docker SSH server with rsync + btrfs.

These tests run serially: the ``test-e2e`` / ``test-e2e-docker`` mise tasks
omit ``-n auto``. Each test spins up a multi-container topology (backup-server
+ bastion, reached via proxy-jump) running the heaviest btrfs + LUKS workload,
so parallelizing the handful of e2e tests would multiply container and
shared-kernel (loop-device / device-mapper) contention for almost no
wall-clock gain. This is a deliberate cost/benefit choice, not a hard blocker
(the Docker network is UUID-named, so per-worker isolation would work). See
``docs/building-and-testing.md`` ("Parallel test execution") and the
``test-e2e-docker`` task in ``mise.toml``.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e_docker
