#!/usr/bin/env python3
"""Runs a project's deploy.py on the host, where it has the access to deploy.

Runs on the host, and so does what it starts: that is the point, since a
deploy usually needs what the container is not given (root on a server, a
password, the host's SSH agent). Which is why it only runs a deploy.py the
container cannot write: one that is a protected path of the project, mounted
read-only (README "Protected files"). Anything else would run on the host
whatever the container last put there.

Before that, it looks for open proposals in protected_draft/ and offers
devcontainer-approve: a deploy runs the protected files as they are, not as
the container proposed them, and a proposal for deploy.py itself means the
deploy that runs is not the one that was asked for.

The script runs in the project directory with the terminal attached, so it can
ask for a user name or a password. PYTHONSAFEPATH keeps the project directory
off Python's import path: deploy.py's "import argparse" must not find an
argparse.py the container left next to it.
"""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from approve import collect  # noqa: E402
from devcontainer_cli import die  # noqa: E402
from protected import protected_paths, read_state, update_base  # noqa: E402

HERE = Path(__file__).resolve().parent
SCRIPT = "deploy.py"


def yes(question, default):
    hint = "[Y/n]" if default else "[y/N]"
    answer = input(f"{question} {hint} ").strip().lower()
    return default if not answer else answer in ("y", "yes")


def is_protected(relative, paths):
    """Whether relative is a protected path or lies inside one."""
    return any(relative == path or relative.startswith(path + "/")
               for path in paths)


def pending(workspace):
    """The protected files with something open in the draft: a proposal, a
    collision, or a deletion marker that points at nothing."""
    update_base(workspace)
    approved = read_state(workspace)
    found = []
    for relative in protected_paths(workspace):
        changes, conflicts, stray = collect(workspace, relative, approved)
        insides = ([inside for inside, _, _ in changes] + conflicts
                   + [f"{marker} (a deletion marker with nothing to delete)"
                      for marker in stray])
        found += [f"{relative}/{inside}" if inside else relative
                  for inside in insides]
    return found


def check_pending(workspace):
    """Offers devcontainer-approve for open proposals; dies if somebody
    would rather not deploy with them still open."""
    open_now = pending(workspace)
    if not open_now:
        return
    print("The container proposed changes to protected files that are not "
          "applied yet:")
    for path in open_now:
        print(f"  {path}")
    if SCRIPT in open_now:
        print(f"One of them is {SCRIPT} itself.")
    if yes("\nReview them now (devcontainer-approve)?", True):
        print(flush=True)
        subprocess.run([sys.executable, str(HERE / "approve.py"),
                        str(workspace)])
        open_now = pending(workspace)
        if not open_now:
            print()
            return
        print(f"\nStill open: {', '.join(open_now)}")
    if not yes("Deploy with the protected files as they are now?", False):
        die("Not deployed.")
    print()


def main():
    argv = sys.argv[1:]
    script_args = []
    if "--" in argv:
        split = argv.index("--")
        argv, script_args = argv[:split], argv[split + 1:]
    if "-h" in argv or "--help" in argv:
        print("usage: devcontainer-deploy [--pause] [PROJECT_DIR] "
              "[-- DEPLOY_ARGS...]\n\n"
              f"Runs PROJECT_DIR's {SCRIPT} on the host (default: current "
              "directory),\nafter offering to apply open proposals for "
              "protected files.\nArguments after -- go to the script, "
              "e.g. -- --dry-run.\n\n"
              "  --pause  wait for Enter before exiting; for a launcher "
              "whose window\n           would otherwise close at once")
        return
    argv = [arg for arg in argv if arg != "--pause"]
    if len(argv) > 1 or any(arg.startswith("-") for arg in argv):
        die(f"Unexpected arguments: {' '.join(argv)} "
            "(arguments for the script go after --)")

    workspace = Path(argv[0] if argv else ".").expanduser()
    if not workspace.is_dir():
        die(f"Project directory not found: {workspace}")
    workspace = workspace.resolve()
    script = workspace / SCRIPT
    if script.is_symlink() or not script.is_file():
        die(f"{workspace} has no {SCRIPT}, so there is nothing to deploy.")
    if not is_protected(SCRIPT, protected_paths(workspace)):
        die(f"{SCRIPT} is not protected in {workspace}: the container can "
            "change it, and this would run whatever it wrote on the host.\n"
            "Mount it read-only in .devcontainer/devcontainer.json, together "
            "with anything it reads or runs from the project "
            '(README "Deploying from the host").')
    if not os.access(script, os.X_OK):
        die(f"{script} is not executable (chmod +x on the host)")

    check_pending(workspace)

    print(f"Running {SCRIPT} in {workspace}\n", flush=True)
    env = dict(os.environ, PYTHONSAFEPATH="1")
    code = subprocess.run([str(script), *script_args], cwd=workspace,
                          env=env).returncode
    if code:
        die(f"\n{SCRIPT} failed (exit code {code})")
    print(f"\n{SCRIPT} finished.")


if __name__ == "__main__":
    pause = "--pause" in sys.argv[1:sys.argv.index("--")
                                  if "--" in sys.argv else None] \
        and sys.stdin.isatty()
    try:
        main()
    except SystemExit as stop:
        if isinstance(stop.code, str):
            print(stop.code, file=sys.stderr)
        if pause:
            input("\nPress Enter to close this window.")
        raise SystemExit(1 if stop.code else 0)
    except KeyboardInterrupt:
        if pause:
            input("\nInterrupted. Press Enter to close this window.")
        raise SystemExit(130)
    if pause:
        input("\nPress Enter to close this window.")
