"""Backup run orchestration.

* :mod:`.pipeline` — the check-then-run pipeline (preflight, then syncs)
* :mod:`.cli` — the ``nbkp run`` command, which wraps the pipeline in the
  mount lifecycle and renders its result
"""
