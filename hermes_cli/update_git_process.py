"""Bound Windows Git helpers as well as the launcher that owns their output pipes."""

import subprocess

from hermes_cli._subprocess_compat import IS_WINDOWS, windows_hide_flags
from hermes_cli.local_runtime.processes import spawn_server


def run_network_git(argv, *, timeout, check=False, capture_output=True, **kwargs):
    if not IS_WINDOWS:
        return subprocess.run(argv, timeout=timeout, check=check,
                              capture_output=capture_output, **kwargs)
    # subprocess.run kills only git.exe, then waits without a bound for inherited
    # pipes. A stalled remote-https/credential helper can hold them indefinitely.
    proc, job = spawn_server(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             creationflags=windows_hide_flags(), **kwargs)
    try:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except BaseException:
            job.terminate_and_wait()
            proc.communicate(timeout=5)
            raise
        result = subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
        if check:
            result.check_returncode()
        return result
    finally:
        job.close()
