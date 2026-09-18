#!/usr/bin/env python3
"""Installs the apt packages that browsers started by Playwright need.

Runs as root while the image is built, started by install.sh next to it. No
browser is downloaded here; each project downloads its own (README "Browsers").
"""

import os
import shutil
import subprocess

# The node feature puts node/npx into PATH via nvm.
environment = dict(os.environ,
                   PATH=f"/usr/local/share/nvm/current/bin:{os.environ['PATH']}")

subprocess.run(["npx", "--yes", "playwright@latest", "install-deps",
                "chromium", "firefox"], check=True, env=environment)

shutil.rmtree("/var/lib/apt/lists", ignore_errors=True)
shutil.rmtree("/root/.npm", ignore_errors=True)
