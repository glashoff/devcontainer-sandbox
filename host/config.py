"""The host's local configuration, which is deliberately not in this repository.

~/.config/devcontainer-sandbox/config holds everything specific to this
machine: the server that dev containers may reach, and the mail account that
reports failed builds. See config.example. The directory around it holds the
rest that must not be in a project: the CA key, the deploy keys and the
projects' Claude Code tokens.
"""

import re
import sys
from pathlib import Path

CONFIG_DIR = Path.home() / ".config/devcontainer-sandbox"
CONFIG_FILE = CONFIG_DIR / "config"
CA_DIR = CONFIG_DIR / "ssh-ca"
CA_KEY = CA_DIR / "ca"
# One Claude Code token per project, named after its folder, written by hand
# from "claude setup-token" (README "Claude Code's login").
TOKEN_DIR = CONFIG_DIR / "claude-tokens"


def read_config(path: Path | None = None) -> dict[str, str]:
    """Reads KEY=VALUE lines. Parsed, never executed."""
    path = path or CONFIG_FILE
    settings: dict[str, str] = {}
    if not path.is_file():
        return settings
    # It holds the SMTP password, so it is nobody else's business.
    if path.stat().st_mode & 0o077:
        print(f"Warning: {path} is readable by others, run: chmod 600 {path}",
              file=sys.stderr)
    for line in path.read_text().splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = re.match(r"\s*([A-Z_]+)\s*=\s*(.*?)\s*$", line)
        if match:
            settings[match.group(1)] = match.group(2)
    return settings


def setting_path(config: dict[str, str], key: str, default: Path) -> Path:
    """A path setting, with ~ expanded."""
    value = config.get(key)
    return Path(value).expanduser() if value else default
