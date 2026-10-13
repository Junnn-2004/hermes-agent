"""Session-end net for the operator's real User PATH on Windows.

``_launchers._register_windows_user_path`` skips the registry under a test
run, but any other writer (a test that strips the isolation markers from a
child's env, a child PowerShell) can still put a temp-dir entry on the real
PATH. Those entries are dead once pytest prunes its basetemp, and they used
to pile up until Explorer, past 4094 characters of machine + user PATH,
dropped the whole user PATH on its next environment refresh.

At session end, real PATH entries under the temp dir that appeared during the
run are removed and reported, and Explorer is told to re-read the environment
so it drops any copy a child's broadcast gave it.

This file sits at the repo root instead of in tests/conftest.py so the local
branch never conflicts with upstream edits there.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Read before tests/conftest.py repoints the temp variables at its sandbox.
# tempfile.gettempdir() is not called here: it caches, and would pin that
# sandbox to the real temp dir.
_TEMP_ROOTS = {os.environ[name] for name in ("TEMP", "TMP") if os.environ.get(name)}
_real_path_at_start: str | None = None


def _read_real_path() -> tuple[str, int] | None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, kind = winreg.QueryValueEx(key, "Path")
            return str(value), int(kind)
    except OSError:
        return None


def pytest_configure(config):
    global _real_path_at_start
    if sys.platform == "win32":
        _real_path_at_start = (_read_real_path() or ("", 0))[0]


def pytest_sessionfinish(session, exitstatus):
    if sys.platform != "win32" or _real_path_at_start is None:
        return
    current = _read_real_path()
    if current is None:
        return
    import ctypes
    import winreg

    before = {p.rstrip("\\/").casefold() for p in _real_path_at_start.split(";") if p}
    temps = [Path(t).resolve() for t in (*_TEMP_ROOTS, tempfile.gettempdir())]
    leaked = [
        p
        for p in current[0].split(";")
        if p
        and p.rstrip("\\/").casefold() not in before
        and any(Path(os.path.expandvars(p)).resolve().is_relative_to(t) for t in temps)
    ]
    if not leaked:
        return
    kept = [p for p in current[0].split(";") if p and p not in leaked]
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, "Path", 0, current[1], ";".join(kept))
    ctypes.windll.user32.SendMessageTimeoutW(
        0xFFFF, 0x1A, 0, "Environment", 0x0002, 5000, None
    )
    message = f"removed {len(leaked)} temp-dir entries a test wrote to the real User PATH: {leaked}"
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(message, yellow=True)
    else:
        print(message, file=sys.stderr)
