"""A dead-stalled network fetch ends `hermes update` with an error, never a hang (#93759, #95777).

`_git_run(network=True)` bounds the wait; a `TimeoutExpired` becomes a failed
CompletedProcess whose stderr names the stall, so every caller's existing
fetch-failure path prints one clear line. Local git (network=False) is unbounded.
"""

import subprocess
import sys
import time
from unittest.mock import MagicMock, patch

import psutil
import pytest

import hermes_cli.update_cmd as update_cmd


@pytest.mark.platforms("windows")
@pytest.mark.parametrize("check", [False, True])
def test_fetch_timeout_reaps_pipe_inheriting_descendant(tmp_path, monkeypatch, check):
    pid_file = tmp_path / "descendant.pid"
    child = (
        "import subprocess,sys,time,pathlib; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(8)']); "
        f"pathlib.Path({str(pid_file)!r}).write_text(str(p.pid)); time.sleep(12)"
    )
    monkeypatch.setattr(update_cmd, "NETWORK_GIT_TIMEOUT_SECONDS", 2)
    started = time.monotonic()
    if check:
        with pytest.raises(subprocess.CalledProcessError) as caught:
            update_cmd._git_run([sys.executable, "-c", child], ["fetch"], cwd=tmp_path,
                                network=True, check=True)
        assert caught.value.returncode == 124
    else:
        result = update_cmd._git_run([sys.executable, "-c", child], ["fetch"], cwd=tmp_path,
                                    network=True)
        assert result.returncode == 124
    assert time.monotonic() - started < 6
    assert not psutil.pid_exists(int(pid_file.read_text()))


def _timeout(cmd, **kwargs):
    if "timeout" in kwargs:
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
    return MagicMock(returncode=0, stdout="ok", stderr="")


def test_network_fetch_stall_becomes_a_failed_run_with_a_named_cause(monkeypatch):
    monkeypatch.setattr(update_cmd, "_m", lambda: MagicMock(PROJECT_ROOT="/repo"))
    with patch.object(update_cmd, "run_network_git", side_effect=_timeout) as run:
        result = update_cmd._git_run(["git"], ["fetch", "origin", "main"], network=True)

    assert result.returncode != 0
    assert "timed out" in result.stderr and "fetch" in result.stderr
    assert run.call_args.kwargs["timeout"] == update_cmd.NETWORK_GIT_TIMEOUT_SECONDS
    # The no-prompt guard still rides along with the bound.
    assert run.call_args.kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"


def test_local_git_stays_unbounded_and_check_true_raises(monkeypatch):
    monkeypatch.setattr(update_cmd, "_m", lambda: MagicMock(PROJECT_ROOT="/repo"))
    with (patch.object(update_cmd.subprocess, "run", side_effect=_timeout) as run,
          patch.object(update_cmd, "run_network_git", side_effect=_timeout)):
        assert update_cmd._git_run(["git"], ["rev-parse", "HEAD"]).returncode == 0
        assert "timeout" not in run.call_args.kwargs

        try:
            update_cmd._git_run(["git"], ["fetch", "origin", "main"], network=True, check=True)
        except subprocess.CalledProcessError as exc:
            assert exc.returncode == 124
        else:
            raise AssertionError("check=True must raise on a timed-out fetch")
