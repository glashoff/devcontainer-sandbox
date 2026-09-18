"""Runs the devcontainer CLI, locally if installed, otherwise in a container
(Linux and Docker only). Imported by start.py and build-image.py."""

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

CLI_VERSION = "0.89.0"
DOCKER = os.environ.get("DOCKER_PATH", "docker")

# python3 is for the projects' initializeCommand (host/initialize.py), which
# the CLI runs in here when there is no local CLI on the host.
CLI_DOCKERFILE = f"""\
FROM node:lts-slim
RUN apt-get update && apt-get install -y --no-install-recommends \\
      docker.io git python3-minimal \\
 && rm -rf /var/lib/apt/lists/*
RUN npm install -g @devcontainers/cli@{CLI_VERSION}
"""
# The tag follows the definition, so a change here builds a new image instead
# of silently keeping the old one.
CLI_TAG = f"{CLI_VERSION}-{hashlib.sha256(CLI_DOCKERFILE.encode()).hexdigest()[:6]}"
CLI_IMAGE = f"local/devcontainer-cli:{CLI_TAG}"


def die(message):
    """Exits with an error message, like the shell scripts' die()."""
    sys.exit(message)


def docker(*args, check=True):
    """Runs docker with its output on the terminal. Returns the exit code."""
    result = subprocess.run([DOCKER, *args])
    if check and result.returncode != 0:
        die(f"{DOCKER} {' '.join(args)} failed")
    return result.returncode


def docker_value(*args):
    """Runs docker and returns its stdout, or None if the command failed.

    For the inspect calls that are allowed to fail (image not present yet).
    """
    result = subprocess.run([DOCKER, *args], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


class Cli:
    """The devcontainer CLI.

    work_dir is made available at the same path inside the CLI container, so
    that bind mounts in devcontainer.json resolve on the host.
    """

    def __init__(self, work_dir):
        self.work_dir = Path(work_dir)
        if not shutil.which(DOCKER):
            die(f"{DOCKER} not found")
        self.local = shutil.which("devcontainer") is not None
        if self.local:
            return
        if sys.platform != "linux" or DOCKER != "docker":
            die("Install the devcontainer CLI: npm install -g @devcontainers/cli")
        self._build_image()
        self.socket = self._docker_socket()

    def run(self, *args, capture=False):
        """Runs a CLI subcommand.

        The CLI writes progress to stderr and its result as JSON to stdout, so
        capture only takes stdout and lets the progress through.
        """
        command = self._command(args)
        result = subprocess.run(command, capture_output=False, text=True,
                                stdout=subprocess.PIPE if capture else None)
        if result.returncode != 0:
            die(f"devcontainer {args[0]} failed")
        return result.stdout.strip() if capture else None

    def _command(self, args):
        if self.local:
            return ["devcontainer", *args, "--docker-path", DOCKER]
        # Same paths inside and outside, so that bind mounts resolve on the
        # host and initializeCommand works on the host's home directory.
        home = Path.home()
        mounts = ["-v", f"{home}:{home}"]
        if home not in self.work_dir.parents:
            mounts += ["-v", f"{self.work_dir}:{self.work_dir}"]
        return [
            DOCKER, "run", "--rm",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--group-add", str(self.socket.stat().st_gid),
            "-e", f"HOME={home}",
            # For the optional Wayland mount of a project.
            "-e", f"XDG_RUNTIME_DIR={os.environ.get('XDG_RUNTIME_DIR', '')}",
            "-e", f"WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY', '')}",
            "-v", f"{self.socket}:/var/run/docker.sock",
            *mounts,
            "-w", str(self.work_dir),
            CLI_IMAGE, "devcontainer", *args,
        ]

    def _build_image(self):
        if docker_value("image", "inspect", CLI_IMAGE) is not None:
            return
        print(f"Building {CLI_IMAGE}")
        result = subprocess.run([DOCKER, "build", "-t", CLI_IMAGE, "-"],
                                input=CLI_DOCKERFILE, text=True)
        if result.returncode != 0:
            die(f"Cannot build {CLI_IMAGE}")
        # Images from an earlier definition keep their tag and would stay on
        # disk (~800 MB each), so drop them once the new one is there.
        listed = docker_value("image", "ls", "local/devcontainer-cli",
                              "--format", "{{.Repository}}:{{.Tag}}") or ""
        for old in listed.splitlines():
            if old != CLI_IMAGE:
                docker_value("image", "rm", old)

    @staticmethod
    def _docker_socket():
        endpoint = os.environ.get("DOCKER_HOST") or docker_value(
            "context", "inspect", "--format", "{{.Endpoints.docker.Host}}")
        if not endpoint:
            die("Cannot determine the Docker socket")
        socket = Path(endpoint.removeprefix("unix://"))
        if not socket.is_socket():
            die(f"Docker socket not found: {socket}")
        return socket
