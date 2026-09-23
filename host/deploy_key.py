"""Read-only GitHub deploy keys for dev containers (README "Fetching from GitHub").

A project whose sandbox.env names a GitHub repository in GIT_REMOTE_URL gets a
key pair of its own, which devcontainer-start creates on the host once and
registers with that repository as a read-only deploy key. The container
fetches that repository over SSH with it, private ones included, but cannot
push. The key stays valid until it is deleted on GitHub.

The repository comes from sandbox.env, never from .git/config: a container
could point origin at another, private repository of the same owner, and the
host would then hand it a key for that one.

Imported by start.py, which creates and installs the key, and initialize.py,
which points the container's gitconfig at it.
"""

from pathlib import Path

from config import CONFIG_DIR
from remote import REMOTE_SETTING, github_repo

KEY_DIR = CONFIG_DIR / "deploy-keys"
# Where devcontainer-start puts the key and GitHub's host keys in the container.
CONTAINER_KEY = "~/.ssh/github_deploy_ed25519"
CONTAINER_KNOWN_HOSTS = "~/.ssh/github_known_hosts"


def key_file(workspace: Path, repo: str) -> Path:
    """The project's private key for repo on the host; the public half is next
    to it. GitHub accepts a key for one repository only, so a project that
    changes its GIT_REMOTE_URL gets a new one."""
    return KEY_DIR / f"github-{workspace.name}-{repo.replace('/', '--')}"


def deploy_repo(settings: dict[str, str]) -> str | None:
    """"owner/repo" to hold a deploy key for, if any.

    None unless GIT_REMOTE_URL is a GitHub repository, or if the project's
    sandbox.env says GIT_DEPLOY_KEY=no.
    """
    if settings.get("GIT_DEPLOY_KEY", "").lower() == "no":
        return None
    return github_repo(settings.get(REMOTE_SETTING))
