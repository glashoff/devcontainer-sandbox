#!/usr/bin/env python3
"""Stops sandbox containers that nobody has used for a while.

Runs every 10 minutes from devcontainer-idle-stop.timer (README "Idle
containers"). A container counts as in use while an SSH session is open in it
or Claude Code runs in it (activity.py). Once it has been idle for
IDLE_STOP_MINUTES (host configuration, default 30, 0 turns this off), it is
stopped, not removed: the next devcontainer-start brings it back, on the
newest image. A project opts out with IDLE_STOP=no in its sandbox.env.
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from activity import activity, sandbox_containers  # noqa: E402
from config import STATE_DIR, read_config  # noqa: E402
from devcontainer_cli import DOCKER  # noqa: E402
from remote import read_settings, sandbox_env  # noqa: E402

DEFAULT_MINUTES = 30
# One file per container, holding the time it was first seen idle.
IDLE_DIR = STATE_DIR / "idle"


def idle_minutes(config):
    value = config.get("IDLE_STOP_MINUTES", "")
    if not value:
        return DEFAULT_MINUTES
    if not value.isdigit():
        sys.exit(f"IDLE_STOP_MINUTES must be a number of minutes, not '{value}'")
    return int(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="show what each container is doing, stop nothing "
                             "and leave the idle times as they are")
    dry_run = parser.parse_args().dry_run

    minutes = idle_minutes(read_config())
    if minutes == 0:
        if dry_run:
            print("IDLE_STOP_MINUTES=0, nothing is stopped")
        return

    IDLE_DIR.mkdir(parents=True, exist_ok=True)
    containers = sandbox_containers()
    # Containers that were stopped or removed some other way.
    for stale in IDLE_DIR.iterdir():
        if stale.name not in containers and not dry_run:
            stale.unlink()

    now = time.time()
    for container_id, folder in containers.items():
        project = Path(folder).name
        idle_file = IDLE_DIR / container_id
        settings = read_settings(sandbox_env(Path(folder)))
        if settings.get("IDLE_STOP", "").lower() == "no":
            if dry_run:
                print(f"{project}: kept running (IDLE_STOP=no)")
            idle_file.unlink(missing_ok=True)
            continue

        in_use = activity(container_id)
        if in_use is None:
            # Never stop what could not be looked at.
            print(f"{project}: cannot list its processes, left alone",
                  file=sys.stderr)
            continue
        if in_use:
            if dry_run:
                print(f"{project}: in use ({in_use})")
            elif idle_file.exists():
                idle_file.unlink()
            continue

        since = float(idle_file.read_text()) if idle_file.exists() else now
        idle_for = (now - since) / 60
        if dry_run:
            print(f"{project}: idle for {idle_for:.0f} of {minutes} minutes")
            continue
        if idle_for < minutes:
            if not idle_file.exists():
                idle_file.write_text(f"{now}\n")
            continue
        print(f"{project}: idle for {idle_for:.0f} minutes, stopping its container")
        result = subprocess.run([DOCKER, "stop", container_id],
                                capture_output=True, text=True)
        if result.returncode != 0:
            print(f"{project}: cannot stop it: {result.stderr.strip()}",
                  file=sys.stderr)
            continue
        idle_file.unlink()


if __name__ == "__main__":
    main()
