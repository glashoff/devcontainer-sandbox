#!/usr/bin/env python3
"""Sets this host up for the dev container sandbox (README "Host setup").

Links ~/.local/share/devcontainer-sandbox to this repository's host/ directory,
creates the devcontainer-start, devcontainer-build-image, devcontainer-push
and devcontainer-approve commands, installs the systemd units and starts the
daily build timer.

Run it once, from the clone. Changes in host/ take effect immediately
afterwards, since nothing is copied.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import CONFIG_DIR, CONFIG_FILE, TOKEN_DIR  # noqa: E402

HERE = Path(__file__).resolve().parent
DEST = Path.home() / ".local/share/devcontainer-sandbox"
BIN = Path.home() / ".local/bin"
UNIT_DIR = Path.home() / ".config/systemd/user"
APPLICATIONS_DIR = Path.home() / ".local/share/applications"

UNITS = ["devcontainer-build-image.service", "devcontainer-build-image.timer"]
COMMANDS = {"devcontainer-start": "start.py",
            "devcontainer-build-image": "build-image.py",
            "devcontainer-push": "push.py",
            "devcontainer-approve": "approve.py"}
# Desktop entry -> the placeholder in it and the script that replaces it.
ENTRIES = {"devcontainer-start.desktop": ("@START@", "start.py"),
           "devcontainer-push.desktop": ("@PUSH@", "push.py"),
           "devcontainer-approve.desktop": ("@APPROVE@", "approve.py")}
TIMER = "devcontainer-build-image.timer"
# Files an earlier version of setup.py copied into DEST instead of linking it.
COPIED_BY_OLD_SETUP = ["start.py", "build-image.py", "initialize.py",
                       "build-finished.py", "setup-ca.py", "setup.py",
                       "devcontainer_cli.py", "config.py", "repo",
                       "__pycache__"]


def install(source: Path, target_dir: Path, mode: int) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    shutil.copyfile(source, target)
    target.chmod(mode)


def link(target: Path, destination: Path) -> None:
    """Replaces destination with a symlink to target."""
    if destination.is_symlink() or destination.exists():
        destination.unlink()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.symlink_to(target)


def replace_old_installation() -> None:
    """Removes what an earlier version copied, so DEST can become a symlink.

    Anything unexpected in there is left alone and reported: it is not this
    script's business to delete files it did not write.
    """
    if DEST.is_symlink() or not DEST.is_dir():
        return
    for name in COPIED_BY_OLD_SETUP:
        entry = DEST / name
        if entry.is_symlink() or entry.is_file():
            entry.unlink()
        elif entry.is_dir():
            shutil.rmtree(entry)
    remaining = sorted(entry.name for entry in DEST.iterdir())
    if remaining:
        sys.exit(f"{DEST} still contains {', '.join(remaining)}. "
                 "Move those aside, then run setup.py again.")
    DEST.rmdir()
    print(f"Removed the copies in {DEST}")


def install_desktop_entries() -> None:
    """Adds the file manager's Open With entries for a project folder.

    A desktop entry cannot expand ~, so the real path is written into it.
    """
    APPLICATIONS_DIR.mkdir(parents=True, exist_ok=True)
    for name, (placeholder, script) in ENTRIES.items():
        entry = APPLICATIONS_DIR / name
        entry.write_text((HERE / name).read_text()
                         .replace(placeholder, str(DEST / script)))
        entry.chmod(0o644)
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(APPLICATIONS_DIR)],
                       capture_output=True)
    print("File manager entries: right-click a project folder, Open With > "
          "Dev container (start it), Git push or Protected files")


def systemctl(*args: str) -> int:
    return subprocess.run(["systemctl", "--user", *args]).returncode


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--no-timer", dest="timer", action="store_false",
                        help="install the units but do not start the daily build")
    parser.add_argument("--server", action="store_true",
                        help="also set up the certificate authority and the "
                             "server (setup-ca.py), which changes the server's "
                             "sshd configuration")
    args = parser.parse_args()

    replace_old_installation()

    # A symlink, not copies: the scripts and the image definition they build
    # from live in the same clone anyway, so copying the scripts would protect
    # only half of what matters (README "Host setup"). The path stays stable,
    # which is what the systemd unit needs.
    link(HERE, DEST)
    print(f"{DEST} -> {HERE}")

    for command, script in COMMANDS.items():
        link(DEST / script, BIN / command)
    print(f"Commands in {BIN}: {', '.join(COMMANDS)}")
    if str(BIN) not in os.environ.get("PATH", "").split(":"):
        print(f"Note: {BIN} is not in PATH", file=sys.stderr)

    install_desktop_entries()

    for name in UNITS:
        install(HERE / name, UNIT_DIR, 0o644)
    systemctl("daemon-reload")
    if args.timer:
        systemctl("enable", "--now", TIMER)
        print(f"Timer {TIMER} enabled")
    else:
        print(f"Units installed, timer not started (systemctl --user enable --now {TIMER})")

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.chmod(0o700)
    # Created here so that the tokens in it are never world-readable, whatever
    # umask the shell that writes them has (README "Claude Code's login").
    TOKEN_DIR.mkdir(exist_ok=True)
    TOKEN_DIR.chmod(0o700)
    if not CONFIG_FILE.exists():
        shutil.copyfile(HERE / "config.example", CONFIG_FILE)
        print(f"Configuration template in {CONFIG_FILE} (server, mail)")
    # It holds the SMTP password, so it is nobody else's business.
    CONFIG_FILE.chmod(0o600)

    if args.server:
        print()
        subprocess.run([sys.executable, str(DEST / "setup-ca.py")], check=True)
    else:
        print(f"\nNext: devcontainer-build-image --full  (first image build)")
        print("For server access from containers: edit the configuration, "
              "then run setup-ca.py")


if __name__ == "__main__":
    main()
