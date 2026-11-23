"""
E2E sync tests — Docker SSH server with rsync + btrfs.

The ``test-e2e-docker`` mise task runs these with ``-n 3``, one worker per
test. Each test brings up its own multi-container topology (backup-server +
bastion, reached via proxy-jump) on a UUID-named Docker network, and the
testkit isolates the shared kernel resources (loop devices, device-mapper
names), so the topologies stay independent. See
``docs/building-and-testing.md`` ("Parallel test execution") and the
``test-e2e-docker`` task in ``mise.toml``.
"""
