"""A failed merge on a custom branch reports git's reason instead of always claiming a conflict."""

import subprocess

import pytest

from hermes_cli import update_cmd


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "core.autocrlf=false", *args],
        check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip()


@pytest.fixture
def custom_branch(tmp_path, monkeypatch):
    for key, value in (("GIT_AUTHOR_NAME", "Test"), ("GIT_AUTHOR_EMAIL", "test@example.invalid"),
                       ("GIT_COMMITTER_NAME", "Test"), ("GIT_COMMITTER_EMAIL", "test@example.invalid"),
                       ("LANG", "C"), ("LC_ALL", "C")):
        monkeypatch.setenv(key, value)
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _git(upstream, "init", "-b", "main")
    (upstream / "base.txt").write_text("base\n", encoding="utf-8")
    _git(upstream, "add", ".")
    _git(upstream, "commit", "-m", "base")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", str(upstream), str(clone))
    _git(clone, "checkout", "-b", "local/custom")
    (clone / "local.txt").write_text("local patch\n", encoding="utf-8")
    _git(clone, "add", ".")
    _git(clone, "commit", "-m", "local patch")
    monkeypatch.setattr("hermes_cli.main.PROJECT_ROOT", clone)
    return upstream, clone


def _reconcile_and_expect_stop(clone):
    before = _git(clone, "rev-parse", "HEAD")
    with pytest.raises(SystemExit) as stopped:
        update_cmd._reconcile_diverged_checkout(["git"], "main", before)
    assert stopped.value.code == 1
    assert _git(clone, "rev-parse", "HEAD") == before
    assert not (clone / ".git" / "MERGE_HEAD").exists()


def test_content_conflict_names_the_conflicting_files(custom_branch, capsys):
    upstream, clone = custom_branch
    (upstream / "base.txt").write_text("upstream edit\n", encoding="utf-8")
    _git(upstream, "commit", "-am", "upstream edit")
    (clone / "base.txt").write_text("local edit\n", encoding="utf-8")
    _git(clone, "commit", "-am", "local edit")
    _git(clone, "fetch", "origin")

    _reconcile_and_expect_stop(clone)

    out = capsys.readouterr().out
    assert "Merge conflict between local commits and upstream" in out
    assert "Conflicting files: base.txt" in out
    assert "git: CONFLICT" in out


def test_merge_failure_without_conflict_reports_gits_reason(custom_branch, capsys):
    upstream, clone = custom_branch
    (upstream / "new.txt").write_text("upstream file\n", encoding="utf-8")
    _git(upstream, "add", ".")
    _git(upstream, "commit", "-m", "upstream adds new.txt")
    _git(clone, "fetch", "origin")
    # An untracked file in the way stops the merge before any content is merged.
    (clone / "new.txt").write_text("untracked local file\n", encoding="utf-8")

    _reconcile_and_expect_stop(clone)

    out = capsys.readouterr().out
    assert "Merge conflict" not in out
    assert "without a content conflict" in out
    assert "untracked working tree files would be overwritten by merge" in out
    assert (clone / "new.txt").read_text(encoding="utf-8") == "untracked local file\n"
