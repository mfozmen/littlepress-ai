"""``littlepress update`` — bring a git-checkout install up to the
latest ``main`` on GitHub.

The package itself is never reinstalled: on Windows the running
``littlepress.exe`` launcher holds its own file open, so pip can't
replace it (and a failed reinstall leaves the package half-removed).
An editable install already runs straight from the checkout, so a
``git pull`` updates the code and only new or changed dependencies
need installing.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

REPO_URL = "https://github.com/mfozmen/littlepress-ai"
ROOT = Path(__file__).resolve().parent.parent


def update(
    root: Path = ROOT,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    echo: Callable[[str], None] = print,
) -> int:
    if not (root / ".git").exists():
        echo(
            "littlepress isn't installed from a git checkout. Close it and run:\n"
            f'  {sys.executable} -m pip install --upgrade "littlepress-ai @ git+{REPO_URL}"'
        )
        return 1

    def git(*args: str) -> subprocess.CompletedProcess:
        return run(["git", "-C", str(root), *args], capture_output=True, text=True)

    if git("status", "--porcelain", "--untracked-files=no").stdout.strip():
        echo(f"{root} has uncommitted changes; commit or stash them, then retry.")
        return 1
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch != "main":
        echo(f"{root} is on '{branch}'; switch to main to update.")
        return 1
    before = git("rev-parse", "HEAD").stdout.strip()
    pull = git("pull", "--ff-only")
    if pull.returncode != 0:
        echo(f"git pull failed:\n{pull.stderr.strip()}")
        return 1
    if git("rev-parse", "HEAD").stdout.strip() == before:
        echo("littlepress is already up to date.")
        return 0
    if not _install_dependencies(root, run, echo):
        echo("Code updated, but installing dependencies failed; see the pip output above.")
        return 1
    echo(f"Updated to {checkout_version(root)}.")
    return 0


def _install_dependencies(root: Path, run, echo) -> bool:
    """Install whatever pip says ``pip install -e root`` would add,
    except littlepress itself."""
    pip = [sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check"]
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        if run(pip + ["--dry-run", "--report", str(report), "-e", str(root)]).returncode:
            return False
        items = json.loads(report.read_text(encoding="utf-8"))["install"]
    needed = [
        f"{i['metadata']['name']}=={i['metadata']['version']}"
        for i in items
        if not i.get("requested")
    ]
    if not needed:
        return True
    echo("Installing new dependencies: " + ", ".join(needed))
    return run(pip + needed).returncode == 0


def checkout_version(root: Path = ROOT) -> str | None:
    """The version in a checkout's ``pyproject.toml`` — the installed
    metadata goes stale after ``update`` because the package isn't
    reinstalled. ``None`` outside a checkout."""
    pyproject = root / "pyproject.toml"
    if not (root / ".git").exists() or not pyproject.is_file():
        return None
    for line in pyproject.read_text(encoding="utf-8").splitlines():
        if line.startswith("version = "):
            return line.split('"')[1]
    return None
