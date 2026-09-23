"""The repository a project belongs to, as its sandbox.env names it.

GIT_REMOTE_URL in .devcontainer/sandbox.env is the one place that says which
repository a project's container gets a read-only deploy key for. The
container cannot change it, since .devcontainer/ is mounted read-only. The
project's .git/config can be rewritten from inside the container, so its
origin is only ever compared with this, never trusted: otherwise a container
could point origin at another, private repository of the same owner and get a
key for that one.

Imported by start.py and initialize.py.
"""

import re
import subprocess
from pathlib import Path

REMOTE_SETTING = "GIT_REMOTE_URL"

GITHUB_URL = re.compile(
    r"(?:git@github\.com:|ssh://git@github\.com/|https://github\.com/)"
    r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$")


def read_settings(path: Path) -> dict[str, str]:
    """Reads .devcontainer/sandbox.env as KEY=VALUE. Parsed, never executed.

    The value is the rest of the line, so that a setting can hold spaces
    (SERVER_SSH_FORCE_COMMAND, GIT_USER_NAME); the same as initialize.py does.
    A comment therefore needs a line of its own.
    """
    settings = {}
    if not path.is_file():
        return settings
    for line in path.read_text().splitlines():
        match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if match:
            settings[match.group(1)] = match.group(2)
    return settings


def sandbox_env(workspace: Path) -> Path:
    return workspace / ".devcontainer/sandbox.env"


def github_repo(url: str | None) -> str | None:
    """"owner/repo" if the URL is a GitHub repository."""
    match = GITHUB_URL.match(url or "")
    return match.group(1) if match else None


def project_git_dir(workspace: Path) -> Path:
    """The project's git directory, found without running git in it.

    In a worktree, .git is a file pointing at the real one, whose commondir
    leads to the shared config.
    """
    dot_git = workspace / ".git"
    if not dot_git.is_file():
        return dot_git
    match = re.match(r"gitdir:\s*(.+)", dot_git.read_text())
    if not match:
        return dot_git
    path = (workspace / match.group(1).strip()).resolve()
    common = path / "commondir"
    return (path / common.read_text().strip()).resolve() if common.is_file() else path


def recorded_origin(workspace: Path) -> str | None:
    """origin as the project's .git/config has it; the container can change it.

    Read from the file alone: --file skips include directives and insteadOf is
    not applied, and git does not run in the project.
    """
    result = subprocess.run(
        ["git", "config", "--file", str(project_git_dir(workspace) / "config"),
         "--get", "remote.origin.url"], capture_output=True, text=True)
    return result.stdout.strip() or None


def origin_mismatch(workspace: Path, remote: str) -> str | None:
    """A warning if .git/config names another repository than sandbox.env."""
    origin = recorded_origin(workspace)
    if origin == remote:
        return None
    repo = github_repo(origin)
    if repo and repo.lower() == (github_repo(remote) or "").lower():
        return None  # the same GitHub repository, written another way
    return (f"origin in .git/config is {origin or '(none)'}, but "
            f"{REMOTE_SETTING} in .devcontainer/sandbox.env is {remote}")
