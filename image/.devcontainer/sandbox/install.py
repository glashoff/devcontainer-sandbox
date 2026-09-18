#!/usr/bin/env python3
"""Installs the sandbox feature: firewall entrypoint, sshd hardening, no sudo.

Runs as root while the image is built, started by install.sh next to it.
"""

import os
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
SANDBOX_DIR = Path("/usr/local/share/sandbox")
SSHD_CONFIG_DIR = Path("/etc/ssh/sshd_config.d")

user = os.environ["_REMOTE_USER"]
user_home = Path(os.environ["_REMOTE_USER_HOME"])

# Debian's sshd_config includes sshd_config.d/*.conf at the top and sshd uses
# the first value it finds, so these settings override the sshd feature.
SSHD_CONFIG = f"""\
# Only the remote user, only with the key bind-mounted from the host.
AllowUsers {user}
AuthorizedKeysFile /etc/ssh/devcontainer/authorized_keys
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no

# Refuse the client's SSH agent even if a client requests forwarding.
AllowAgentForwarding no
X11Forwarding no
PermitTunnel no
GatewayPorts no
"""


def make_dir(path, mode, owner="root"):
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(mode)
    shutil.chown(path, owner, owner)


def main():
    subprocess.run(["apt-get", "update"], check=True)
    subprocess.run(["apt-get", "install", "-y", "--no-install-recommends", "iptables"],
                   check=True)
    shutil.rmtree("/var/lib/apt/lists", ignore_errors=True)

    make_dir(SANDBOX_DIR, 0o755)
    for script in ("init-firewall.py", "check-ssh-agent.py"):
        target = SANDBOX_DIR / script
        shutil.copy(HERE / script, target)
        target.chmod(0o755)
        shutil.chown(target, "root", "root")

    # Volumes (devcontainer-feature.json "mounts"): the Claude Code login, and
    # VSCodium's remote server with its extensions, so they survive the daily
    # rebuild. Claude Code's own auto-updater is off (DISABLE_AUTOUPDATER): it
    # cannot work without root, the daily image build updates it instead.
    #
    # Pre-create the mount points so a fresh volume inherits the ownership
    # (there is no sudo later to fix it).
    make_dir(user_home / ".claude", 0o700, user)
    make_dir(user_home / ".vscodium-server", 0o700, user)

    # XDG_RUNTIME_DIR for the optional Wayland passthrough (README "Wayland").
    make_dir(Path(f"/tmp/runtime-{user}"), 0o700, user)

    make_dir(SSHD_CONFIG_DIR, 0o755)
    config = SSHD_CONFIG_DIR / "10-sandbox.conf"
    config.write_text(SSHD_CONFIG)
    config.chmod(0o644)

    # Mount point for the public key (root-owned, as required by StrictModes).
    make_dir(Path("/etc/ssh/devcontainer"), 0o755)

    # The remote user must not be able to become root, otherwise it could
    # simply flush the firewall rules. Features installed after this one may
    # add sudoers entries again, so the entrypoint repeats this at every
    # container start.
    for entry in Path("/etc/sudoers.d").glob("*"):
        entry.unlink()


if __name__ == "__main__":
    main()
