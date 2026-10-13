"""Keep Windows test runs off the operator's real ``HKCU\\Environment``.

Update and launcher code registers ``$HERMES_HOME\\bin`` on the User PATH
through winreg (``_launchers._register_windows_user_path``). Under pytest
HERMES_HOME is a temp dir, so every Windows run appended dead
``...\\hermes_test\\bin`` entries to the real PATH: one run of
tests/hermes_cli/test_update_parked_branch_guard.py adds seven. Once machine +
user PATH passes 4094 characters, Explorer's environment refresh drops the
whole user PATH, and every shell opened afterwards loses the per-user tools.

Each test sees ``HKCU\\Environment`` through a per-process scratch key seeded
with a copy of the real values, and environment-change broadcasts are muted.
At session end, any real PATH entry under the temp dir that appeared during
the run (a writer the redirect cannot see, such as a child PowerShell) is
removed and reported. tests/e2e keeps the real key: its harness saves and
restores it around each run.

This file sits at the repo root instead of in tests/conftest.py so the local
branch never conflicts with upstream edits there.
"""

from __future__ import annotations

import functools
import os
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent
_E2E = _ROOT / "tests" / "e2e"
_SCRATCH_PARENT = r"Software\HermesTestSandbox"
_SCRATCH = rf"{_SCRATCH_PARENT}\Environment-{os.getpid()}"
_HWND_BROADCAST = 0xFFFF
_WM_SETTINGCHANGE = 0x1A
# Read before tests/conftest.py repoints the temp variables at its sandbox.
# tempfile.gettempdir() is not called here: it caches, and would pin that sandbox
# to the real temp dir.
_TEMP_ROOTS = {os.environ[name] for name in ("TEMP", "TMP") if os.environ.get(name)}
_real_path_at_start: tuple[str, int] | None = None


def _read_real_path() -> tuple[str, int] | None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, kind = winreg.QueryValueEx(key, "Path")
            return str(value), int(kind)
    except OSError:
        return None


def _seed_scratch(real_open) -> None:
    import winreg

    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, _SCRATCH, 0, winreg.KEY_ALL_ACCESS
    ) as dst:
        while True:
            try:
                winreg.DeleteValue(dst, winreg.EnumValue(dst, 0)[0])
            except OSError:
                break
        with real_open(
            winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ
        ) as src:
            index = 0
            while True:
                try:
                    name, value, kind = winreg.EnumValue(src, index)
                except OSError:
                    break
                winreg.SetValueEx(dst, name, 0, kind, value)
                index += 1


def _redirected(open_fn):
    import winreg

    @functools.wraps(open_fn)
    def opener(key, *args, **kwargs):
        if args:
            sub_key, rest = args[0], args[1:]
        else:
            sub_key, rest = kwargs.pop("sub_key"), ()
        if (
            isinstance(key, int)
            and key == winreg.HKEY_CURRENT_USER
            and isinstance(sub_key, str)
            and sub_key.strip("\\").casefold() == "environment"
        ):
            sub_key = _SCRATCH
        return open_fn(key, sub_key, *rest, **kwargs)

    return opener


def _muted(send):
    @functools.wraps(send)
    def sender(hwnd, msg, *args):
        if hwnd == _HWND_BROADCAST and msg == _WM_SETTINGCHANGE:
            return 1
        return send(hwnd, msg, *args)

    return sender


@pytest.fixture(autouse=True)
def _sandbox_windows_user_environment(request, monkeypatch):
    if sys.platform != "win32" or Path(str(request.node.path)).resolve().is_relative_to(
        _E2E
    ):
        yield
        return
    import ctypes
    import winreg

    _seed_scratch(winreg.OpenKey)
    for name in ("OpenKey", "OpenKeyEx", "CreateKey", "CreateKeyEx"):
        monkeypatch.setattr(winreg, name, _redirected(getattr(winreg, name)))
    user32 = ctypes.windll.user32
    monkeypatch.setattr(
        user32, "SendMessageTimeoutW", _muted(user32.SendMessageTimeoutW)
    )
    yield


def pytest_configure(config):
    global _real_path_at_start
    if sys.platform == "win32":
        _real_path_at_start = _read_real_path()


def pytest_sessionfinish(session, exitstatus):
    if sys.platform != "win32":
        return
    import winreg

    for key in (_SCRATCH, _SCRATCH_PARENT):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except OSError:
            pass  # never created, or another run's scratch key is still there
    current = _read_real_path()
    if current is None:
        return
    before = {
        p.rstrip("\\/").casefold()
        for p in (_real_path_at_start or ("", 0))[0].split(";")
        if p
    }
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
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    message = f"removed {len(leaked)} temp-dir entries a test wrote to the real User PATH: {leaked}"
    if reporter is not None:
        reporter.write_line(message, yellow=True)
    else:
        print(message, file=sys.stderr)
