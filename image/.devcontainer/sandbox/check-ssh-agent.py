#!/usr/bin/env python3
"""Runs on every attach (postAttachCommand of the sandbox feature). Warns
loudly and removes the socket if a host SSH agent was forwarded into the
container anyway.
"""

import os
import sys
from pathlib import Path

candidates = []
if os.environ.get("SSH_AUTH_SOCK"):
    candidates.append(Path(os.environ["SSH_AUTH_SOCK"]))
candidates += sorted(Path("/tmp").glob("vscode-ssh-auth-*.sock"))

found = [socket for socket in candidates if socket.is_socket()]
for socket in found:
    socket.unlink()

if found:
    print("\n\033[1;41m WARNING: host SSH agent was forwarded into the container! \033[0m")
    print("Removed socket(s): " + " ".join(str(socket) for socket in found))
    print("Start VS Code via the launcher that unsets SSH_AUTH_SOCK "
          "and reopen the container.\n")
    sys.exit(1)

print("sandbox: no host SSH agent forwarded")
