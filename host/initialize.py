#!/usr/bin/env python3
"""Runs on the host before the container is created (initializeCommand of every
project, and start.py). Docker refuses to start the container if a bind-mount
source is missing, so create the files referenced in devcontainer.json "mounts".

The project folder is the first argument, or the working directory, which is
what the devcontainer CLI runs initializeCommand in.
"""

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import CONFIG_DIR, read_config  # noqa: E402
from deploy_key import (CONTAINER_KEY, CONTAINER_KNOWN_HOSTS,  # noqa: E402
                        deploy_repo, key_file)
from remote import REMOTE_SETTING  # noqa: E402

home = Path.home()
workspace = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()

claude_md = home / ".claude/CLAUDE.md"
if not claude_md.exists():
    claude_md.parent.mkdir(parents=True, exist_ok=True)
    claude_md.touch()

# Dedicated key for SSH login into dev containers. It only grants access to
# containers that mount its public half, so it has no passphrase.
key = home / ".ssh/devcontainer_ed25519"
if not key.with_name(key.name + ".pub").is_file():
    key.parent.mkdir(parents=True, exist_ok=True)
    key.parent.chmod(0o700)
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "",
                    "-C", "devcontainer", "-f", str(key)], check=True)


def git_identity(setting: str) -> str:
    """One setting from the host's git configuration, empty if it is unset."""
    result = subprocess.run(["git", "config", "--global", "--get", setting],
                            capture_output=True, text=True)
    return result.stdout.strip()


def project_settings() -> dict[str, str]:
    """GIT_* settings from the project's .devcontainer/sandbox.env."""
    settings = {}
    sandbox_env = workspace / ".devcontainer/sandbox.env"
    if sandbox_env.is_file():
        for line in sandbox_env.read_text().splitlines():
            match = re.match(r"\s*(GIT_[A-Z_]+)\s*=\s*(.*?)\s*$", line)
            if match and not line.lstrip().startswith("#"):
                settings[match.group(1)] = match.group(2)
    return settings


# Just enough git configuration to commit inside the container, and to fetch
# from GitHub. Credential helpers are never taken along: pushing stays a job
# for the host.
#
# SSH needs a key even for a public repository, so GitHub remotes written as
# git@github.com:... are fetched over HTTPS instead, which needs none. The
# rewrite lives only in this file; the project's .git/config is shared with
# the host and stays as it is.
#
# The name and email come from the project's sandbox.env, else from the local
# configuration, else from this host's git identity. One file per project, so
# that starting one project does not change the identity under another
# project's running container. Rewritten in place on every start.
host_settings = read_config()
settings = project_settings()
name = (settings.get("GIT_USER_NAME") or host_settings.get("GIT_USER_NAME")
        or git_identity("user.name"))
email = (settings.get("GIT_USER_EMAIL") or host_settings.get("GIT_USER_EMAIL")
         or git_identity("user.email"))

lines = ["# Written by the host (initialize.py of devcontainer-sandbox) and",
         "# mounted read-only. No credentials.",
         "[init]", "\tdefaultBranch = main",
         '[url "https://github.com/"]',
         "\tinsteadOf = git@github.com:",
         "\tinsteadOf = ssh://git@github.com/"]
if name and email:
    lines += ["[user]", f"\tname = {name}", f"\temail = {email}"]

# The project's own repository (GIT_REMOTE_URL in sandbox.env) goes over SSH
# instead, with its read-only deploy key (deploy_key.py), once
# devcontainer-start has registered one. The longer insteadOf wins over the
# HTTPS rewrite above. Only forms ending in .git besides the configured one,
# since a shorter prefix would also catch owner/repo-other.
repo = deploy_repo(settings)
if repo and key_file(workspace, repo).is_file():
    forms = dict.fromkeys([settings[REMOTE_SETTING],
                           f"git@github.com:{repo}.git",
                           f"ssh://git@github.com/{repo}.git",
                           f"https://github.com/{repo}.git"])
    lines += [f'[url "git@github.com:{repo}.git"]']
    lines += [f"\tinsteadOf = {form}" for form in forms]
    lines += ["[core]",
              f"\tsshCommand = ssh -i {CONTAINER_KEY} -o IdentitiesOnly=yes"
              f" -o UserKnownHostsFile={CONTAINER_KNOWN_HOSTS}"
              " -o StrictHostKeyChecking=yes"]
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
(CONFIG_DIR / f"gitconfig-{workspace.name}").write_text("\n".join(lines) + "\n")
