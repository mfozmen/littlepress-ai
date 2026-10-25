import json
import subprocess
import sys

from src import cli
from src.update import checkout_version, update


class _FakeRun:
    """Records commands, answers git queries from a script and writes
    the pip ``--report`` file."""

    def __init__(self, heads=("aaa", "bbb"), status="", branch="main", rc=None, install=()):
        self.calls = []
        self._heads = list(heads)
        self._status = status
        self._branch = branch
        self._rc = rc or {}
        self._install = list(install)

    def __call__(self, cmd, **_kwargs):
        self.calls.append(cmd)
        key = " ".join(str(c) for c in cmd)
        out = ""
        if "status" in cmd:
            out = self._status
        elif "--abbrev-ref" in cmd:
            out = self._branch
        elif "rev-parse" in cmd:
            out = self._heads.pop(0)
        elif "--report" in cmd:
            report = {"install": [{"metadata": {"name": "littlepress-ai", "version": "9.0.0"}, "requested": True}]}
            report["install"] += [
                {"metadata": {"name": n, "version": v}} for n, v in self._install
            ]
            with open(cmd[cmd.index("--report") + 1], "w", encoding="utf-8") as f:
                json.dump(report, f)
        rc = next((v for k, v in self._rc.items() if k in key), 0)
        return subprocess.CompletedProcess(cmd, rc, stdout=out + "\n", stderr="")


def _checkout(tmp_path, version="9.0.0"):
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "littlepress-ai"\nversion = "{version}"\n', encoding="utf-8"
    )
    return tmp_path


def _pip_installs(run):
    return [c for c in run.calls if "pip" in c and "--dry-run" not in c]


def test_checkout_pulls_and_reports_the_new_version(tmp_path):
    root = _checkout(tmp_path)
    run = _FakeRun()
    lines = []

    assert update(root, run=run, echo=lines.append) == 0

    assert ["git", "-C", str(root), "pull", "--ff-only"] in run.calls
    assert lines[-1] == "Updated to 9.0.0."


def test_new_dependencies_are_installed_but_never_littlepress_itself(tmp_path):
    # Reinstalling littlepress would rewrite the running littlepress.exe,
    # which Windows refuses.
    run = _FakeRun(install=[("rich", "15.0.0")])

    assert update(_checkout(tmp_path), run=run, echo=lambda _: None) == 0

    assert _pip_installs(run) == [[sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check", "rich==15.0.0"]]


def test_no_new_dependencies_means_no_install(tmp_path):
    run = _FakeRun()

    update(_checkout(tmp_path), run=run, echo=lambda _: None)

    assert _pip_installs(run) == []


def test_checkout_already_current_skips_pip(tmp_path):
    run = _FakeRun(heads=("aaa", "aaa"))
    lines = []

    assert update(_checkout(tmp_path), run=run, echo=lines.append) == 0

    assert not any("pip" in c for c in run.calls)
    assert "up to date" in lines[-1]


def test_checkout_with_local_changes_is_left_alone(tmp_path):
    run = _FakeRun(status=" M src/cli.py")

    assert update(_checkout(tmp_path), run=run, echo=lambda _: None) == 1

    assert not any("pull" in c for c in run.calls)


def test_checkout_on_another_branch_is_left_alone(tmp_path):
    run = _FakeRun(branch="feat/x")
    lines = []

    assert update(_checkout(tmp_path), run=run, echo=lines.append) == 1

    assert not any("pull" in c for c in run.calls)
    assert "main" in lines[-1]


def test_failed_pull_stops_before_pip(tmp_path):
    run = _FakeRun(rc={"pull --ff-only": 1})

    assert update(_checkout(tmp_path), run=run, echo=lambda _: None) == 1

    assert not any("pip" in c for c in run.calls)


def test_failed_dependency_check_is_reported(tmp_path):
    run = _FakeRun(rc={"--dry-run": 1})
    lines = []

    assert update(_checkout(tmp_path), run=run, echo=lines.append) == 1

    assert "failed" in lines[-1]


def test_failed_dependency_install_is_reported(tmp_path):
    run = _FakeRun(install=[("rich", "15.0.0")], rc={"rich==": 1})
    lines = []

    assert update(_checkout(tmp_path), run=run, echo=lines.append) == 1

    assert "failed" in lines[-1]


def test_plain_install_prints_the_upgrade_command(tmp_path):
    run = _FakeRun()
    lines = []

    assert update(tmp_path, run=run, echo=lines.append) == 1

    assert run.calls == []
    assert "git+https://github.com/mfozmen/littlepress-ai" in lines[-1]


def test_checkout_version_reads_pyproject(tmp_path):
    assert checkout_version(_checkout(tmp_path, "1.2.3")) == "1.2.3"


def test_checkout_version_is_none_outside_a_checkout(tmp_path):
    (tmp_path / "pyproject.toml").write_text('version = "1.2.3"\n', encoding="utf-8")

    assert checkout_version(tmp_path) is None


def test_checkout_version_is_none_without_a_version_line(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    assert checkout_version(tmp_path) is None


def test_cli_update_subcommand_runs_update(monkeypatch):
    seen = []
    monkeypatch.setattr("src.update.update", lambda: seen.append(1) or 0)

    assert cli.main(["update"]) == 0
    assert len(seen) == 1


def test_cli_version_prefers_the_checkout_version(monkeypatch):
    monkeypatch.setattr("src.update.checkout_version", lambda: "7.7.7")

    assert cli._resolve_version() == "7.7.7"
