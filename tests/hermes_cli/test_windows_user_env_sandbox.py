"""A test run never registers its temp HERMES_HOME on the operator's real User PATH."""

from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = pytest.mark.platforms("windows")


def _real_user_path() -> str:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        return winreg.QueryValueEx(key, "Path")[0]


def test_registration_in_the_test_process_leaves_the_real_path_alone(tmp_path):
    from hermes_cli import _launchers

    before = _real_user_path()
    assert (
        _launchers._register_windows_user_path(tmp_path / "hermes_test" / "bin")
        == "test-isolated"
    )
    assert _real_user_path() == before


def test_registration_in_a_child_process_leaves_the_real_path_alone(tmp_path):
    before = _real_user_path()
    code = (
        "import sys; from pathlib import Path; from hermes_cli import _launchers; "
        "print(_launchers._register_windows_user_path(Path(sys.argv[1])))"
    )
    child = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "hermes_test" / "bin")],
        capture_output=True,
        text=True,
        check=True,
    )
    assert child.stdout.strip() == "test-isolated"
    assert _real_user_path() == before
