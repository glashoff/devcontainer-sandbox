#!/usr/bin/env python3
"""Stops sandbox containers that nobody has used for a while.

Runs every 10 minutes from devcontainer-idle-stop.timer (README "Idle
containers"). A container counts as in use while an SSH session is open in it
or Claude Code runs in it (activity.py). Idle time counts from whichever is
later: the first look that found it unused, or the last trace Claude Code
wrote, since a Claude run can begin and end between two looks. Once it has
been idle for IDLE_STOP_MINUTES (host configuration, default 30, 0 turns this
off), it is stopped, not removed: the next devcontainer-start brings it back,
on the newest image. A project opts out with IDLE_STOP=no in its sandbox.env.

devcontainer-stop-idle --now stops every idle container right away.
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from activity import activity, last_claude_activity, sandbox_containers  # noqa: E402
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


def idle_since(container_id, first_seen):
    """Since when the container is idle, as a Unix time.

    What keeps it in use instead, as a short text (activity.py), if it is.

    first_seen is when a look first found it unused. None if the container
    could not be looked at, which never counts as idle.
    """
    in_use = activity(container_id)
    if in_use is None or in_use:
        return in_use
    last_trace = last_claude_activity(container_id)
    if last_trace is None:
        return None
    return max(first_seen, last_trace)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="show what each container is doing, stop nothing "
                             "and leave the idle times as they are")
    parser.add_argument("--now", action="store_true",
                        help="stop every container that is idle right now, "
                             "without waiting; also with IDLE_STOP_MINUTES=0")
    args = parser.parse_args()
    dry_run = args.dry_run

    minutes = 0 if args.now else idle_minutes(read_config())
    if minutes == 0 and not args.now:
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

        first_seen = float(idle_file.read_text()) if idle_file.exists() else now
        since = idle_since(container_id, first_seen)
        if since is None:
            # Never stop what could not be looked at.
            print(f"{project}: cannot look inside it, left alone",
                  file=sys.stderr)
            continue
        if isinstance(since, str):
            if dry_run:
                print(f"{project}: in use ({since})")
            elif idle_file.exists():
                idle_file.unlink()
            continue

        idle_for = max(now - since, 0) / 60
        if dry_run:
            print(f"{project}: idle for {idle_for:.0f} of {minutes} minutes")
            continue
        if idle_for < minutes:
            if not idle_file.exists():
                idle_file.write_text(f"{now}\n")
            continue
        # Looked at once more right before stopping: a Claude may have started
        # while the other containers were being looked at.
        again = idle_since(container_id, first_seen)
        if again is None or isinstance(again, str) or (time.time() - again) / 60 < minutes:
            print(f"{project}: in use again, left running")
            continue
        print(f"{project}: idle" + (f" for {idle_for:.0f} minutes" if idle_for else "")
              + ", stopping its container")
        result = subprocess.run([DOCKER, "stop", container_id],
                                capture_output=True, text=True)
        if result.returncode != 0:
            print(f"{project}: cannot stop it: {result.stderr.strip()}",
                  file=sys.stderr)
            continue
        idle_file.unlink()


if __name__ == "__main__":
    main()
