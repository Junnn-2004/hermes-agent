"""The repo-root conftest keeps Windows test runs off the real HKCU\\Environment."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.platforms("windows")


def _real_user_path() -> str:
    import winreg

    with winreg.OpenKey.__wrapped__(winreg.HKEY_CURRENT_USER, "Environment") as key:
        return winreg.QueryValueEx(key, "Path")[0]


def test_user_path_registration_stays_in_the_sandbox(tmp_path):
    import winreg

    from hermes_cli import _launchers

    entry = tmp_path / "hermes_test" / "bin"
    real_before = _real_user_path()

    assert _launchers._register_windows_user_path(entry) == "added"

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        assert winreg.QueryValueEx(key, "Path")[0].split(";")[0] == str(entry)
    assert _real_user_path() == real_before


def test_environment_broadcast_is_muted():
    import ctypes

    send = ctypes.windll.user32.SendMessageTimeoutW
    assert hasattr(send, "__wrapped__")
    assert send(0xFFFF, 0x1A, 0, "Environment", 0x0002, 5000, None) == 1
