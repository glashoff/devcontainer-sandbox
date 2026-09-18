#!/usr/bin/env python3
"""Builds the sandbox image local/devcontainer-sandbox:latest on this host.

  1. Base: tools and sandbox from image/.devcontainer, built with the Docker
     cache, so its layers only change when a tool is added, and fully rebuilt
     without cache once a week (or with --full).
  2. Updates: image/refresh.Dockerfile on top, rebuilt daily (apt upgrade,
     Claude Code). Only this small layer is new on an ordinary day.

The previous image is kept as local/devcontainer-sandbox:previous for rolling
back. Runs daily from devcontainer-build-image.timer (see README.md).
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from devcontainer_cli import Cli, die, docker, docker_value  # noqa: E402

IMAGE = "local/devcontainer-sandbox"
BASE_IMAGE = "local/devcontainer-sandbox-base"
FULL_EVERY_DAYS = 7
STATE_DIR = Path(
    os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state"
) / "devcontainer-sandbox"


def base_image_of(dockerfile):
    """The FROM image of the Dockerfile, so the name is defined in one place."""
    for line in dockerfile.read_text().splitlines():
        match = re.match(r"FROM\s+(\S+)", line)
        if match:
            return match.group(1)
    die(f"No FROM line in {dockerfile}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--full", action="store_true",
                        help="rebuild everything without cache "
                             f"(otherwise done every {FULL_EVERY_DAYS} days)")
    full = parser.parse_args().full

    # The installed scripts are a symlink to this repository's host/ directory
    # (setup.py), so the image definition is the neighbour of our own folder.
    repo = Path(__file__).resolve().parent.parent
    dockerfile = repo / "image/.devcontainer/Dockerfile"
    if not (repo / "image/.devcontainer/devcontainer.json").is_file():
        die(f'No image definition in {repo}, see README.md "Host setup"')

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    last_full_file = STATE_DIR / "last-full-build"
    last_full = int(last_full_file.read_text()) if last_full_file.exists() else 0
    if (time.time() - last_full) / 86400 >= FULL_EVERY_DAYS:
        full = True

    cli = Cli(repo)

    if docker_value("image", "inspect", f"{IMAGE}:latest") is not None:
        docker("tag", f"{IMAGE}:latest", f"{IMAGE}:previous")

    if full:
        # --no-cache re-runs every step, but it does not fetch a newer base
        # image (the devcontainer CLI has no --pull), so without this the FROM
        # image would stay at the digest it was first pulled with.
        # The CLI writes devcontainer-lock.json with the exact feature digests
        # and reuses them afterwards (that is what "devcontainer upgrade" is
        # for), so without this the features would stay on the versions of the
        # very first build. Dropping it is what makes a full build full.
        lock_file = repo / "image/.devcontainer/devcontainer-lock.json"
        lock_file.unlink(missing_ok=True)

        from_image = base_image_of(dockerfile)
        print(f"Pulling {from_image}")
        if docker_value("pull", "-q", from_image) is None:
            print(f"Warning: cannot pull {from_image}, building on the local copy",
                  file=sys.stderr)
        print(f"Full build of {BASE_IMAGE} (no cache)")
        cli.run("build", "--workspace-folder", str(repo / "image"),
                "--image-name", f"{BASE_IMAGE}:latest", "--no-cache")
    else:
        print(f"Build of {BASE_IMAGE} (cached)")
        cli.run("build", "--workspace-folder", str(repo / "image"),
                "--image-name", f"{BASE_IMAGE}:latest")

    # The date busts the cache of the update layer once per day.
    print(f"Daily updates on top: {IMAGE}:latest")
    docker("build", "--tag", f"{IMAGE}:latest",
           "--build-arg", f"BASE={BASE_IMAGE}:latest",
           "--build-arg", f"REFRESH={time.strftime('%Y-%m-%d')}",
           "--file", str(repo / "image/refresh.Dockerfile"), str(repo / "image"))

    if full:
        last_full_file.write_text(str(int(time.time())))

    # Old builds are untagged now; remove those no container uses any more.
    docker_value("image", "prune", "-f")
    print(f"Done: {IMAGE}:latest ({time.strftime('%Y-%m-%d %H:%M')})")


if __name__ == "__main__":
    main()
