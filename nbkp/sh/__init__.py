"""Generate a standalone bash script from nbkp config (see :mod:`nbkp.sh.script`)."""

from nbkp.sh.script import ScriptOptions, VolumeWithoutPathError, generate_script

__all__ = ["ScriptOptions", "VolumeWithoutPathError", "generate_script"]
