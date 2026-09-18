#!/usr/bin/env python3
"""Runs on the host before the container is created (initializeCommand of every
project, and start.py). Docker refuses to start the container if a bind-mount
source is missing, so create the files referenced in devcontainer.json "mounts".
"""

import subprocess
from pathlib import Path

home = Path.home()

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
