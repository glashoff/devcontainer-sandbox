#!/usr/bin/env python3
"""Pushes a project's current branch to its remote, fast-forward only.

Runs on the host. Nothing a container wrote is executed: the project's .git is
writable from inside its container, so running git in it on the host would run
its hooks and its config (core.hooksPath, core.sshCommand, credential.helper, a
forced push refspec, ...). Instead, the commits are fetched into a separate
repository that belongs to this script (~/.local/state/devcontainer-sandbox/
push/), and pushed from there. Only git upload-pack reads the project's
repository, as it would to serve a clone; git runs it in untrusted repositories
by design.

It pushes to GIT_REMOTE_URL from the project's .devcontainer/sandbox.env,
which the container cannot write, never to the origin of .git/config.

The push never merges and never overwrites: if the remote has commits the
project does not have, it stops, and the project has to fetch and merge first
(which works inside the container, README "Fetching from GitHub").

Before asking, it shows what would be pushed, and warns about what a container
might slip in: CI configuration, files that look like credentials, new
executables, large files, submodules, commits under another identity, and
the history of another repository.
Git LFS is refused, since its objects are uploaded by a hook, which is exactly
what this script does not run.
"""

import argparse
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import CONFIG_DIR  # noqa: E402
from protected import DRAFT_DIR  # noqa: E402
from remote import (REMOTE_SETTING, origin_mismatch, read_settings,  # noqa: E402
                    recorded_origin, sandbox_env)

STATE_DIR = Path(os.environ.get("XDG_STATE_HOME")
                 or Path.home() / ".local/state") / "devcontainer-sandbox"

# git's empty tree, to compare a branch with no known base against.
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
# GitHub warns from 50 MB per file and refuses 100 MB.
LARGE_FILE_BYTES = 50 * 1024 * 1024
SHOWN_COMMITS = 30
SHOWN_FILES = 40
CREDENTIAL_NAME = re.compile(
    r"^(\.env(\..+)?|\.netrc|\.pgpass|\.npmrc|\.pypirc|credentials(\.json)?"
    r"|id_(rsa|dsa|ecdsa|ed25519)|.+\.(pem|key|p12|pfx|jks|keystore|kdbx|ovpn))$")
CI_FILE = re.compile(r"^(\.github/workflows/|\.gitlab-ci\.yml$)")
GIT_BEHAVIOUR_FILE = re.compile(r"(^|/)\.git(modules|attributes)$")
SANDBOX_FILE = re.compile(r"^\.devcontainer/")


def die(message):
    sys.stdout.flush()  # what was printed so far comes before the message
    sys.exit(message)


def git(*args, check=True):
    """Runs git; returns stdout. Never in the project, always with -C MIRROR."""
    result = subprocess.run(["git", *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        die(f"git {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout.strip() if result.returncode == 0 else None


def write_remote(env_file, url):
    """Writes GIT_REMOTE_URL to the project's sandbox.env, creating it if needed.

    The template ships the setting empty, so that line is filled in where it
    stands, with its comment above it. Only a file without it gets one
    appended; two assignments would leave it unclear which one counts.
    """
    if not re.fullmatch(r"[^\s#]+", url):
        die(f"Cannot write this URL to {env_file}: {url!r}")
    text = env_file.read_text() if env_file.is_file() else ""
    empty = re.compile(rf"^[ \t]*{REMOTE_SETTING}[ \t]*=[ \t]*$", re.MULTILINE)
    if empty.search(text):
        text = empty.sub(f"{REMOTE_SETTING}={url}", text, count=1)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += ("\n# The repository this project belongs to: devcontainer-push pushes\n"
                 "# only there, and the container gets a read-only deploy key for it.\n"
                 f"{REMOTE_SETTING}={url}\n")
    env_file.parent.mkdir(exist_ok=True)
    env_file.write_text(text)
    print(f"Written to {env_file}; commit it with the project.")


def known_commit(mirror, commit):
    """Whether the mirror has this commit, i.e. the project's history has it."""
    return git("-C", str(mirror), "cat-file", "-e", f"{commit}^{{commit}}",
               check=False) is not None


def fork_point(mirror, new):
    """For a new branch: where it leaves the remote's default branch, if the
    project has that. Otherwise None, and the whole history is shown."""
    listed = git("-C", str(mirror), "ls-remote", "origin", "HEAD")
    default = listed.split()[0] if listed else None
    if default and known_commit(mirror, default):
        return git("-C", str(mirror), "merge-base", default, new, check=False)
    return None


def own_emails(workspace):
    """The identities commits are expected under: the one the project's
    container commits with (initialize.py), and the host's."""
    emails = set()
    for args in (["--file", str(CONFIG_DIR / f"gitconfig-{workspace.name}")],
                 ["--global"]):
        email = git("config", *args, "--get", "user.email", check=False)
        if email:
            emails.add(email.lower())
    return emails


def review_commits(mirror, base, new, workspace):
    """Prints the commits to be pushed; returns warnings about their authors."""
    log = git("-C", str(mirror), "log", "--no-decorate",
              "--format=%h%x00%an <%ae>%x00%ae%x00%cn <%ce>%x00%ce%x00%s",
              f"{base}..{new}" if base else new)
    commits = [line.split("\0") for line in log.splitlines()]
    print(f"  {len(commits)} commit{'s' if len(commits) != 1 else ''}"
          + ("" if base else " (the whole history: no common base with the remote)")
          + "\n")
    for short, author, _, _, _, subject in commits[:SHOWN_COMMITS]:
        print(f"  {short} {subject}")
    if len(commits) > SHOWN_COMMITS:
        print(f"  ... and {len(commits) - SHOWN_COMMITS} more")

    # A commit without parents starts a history of its own. An ordinary branch
    # never brings one along; another repository does, whether it replaces the
    # branch or is merged in with --allow-unrelated-histories. Only the first
    # push into an empty repository is expected to have one.
    warnings = []
    roots = git("-C", str(mirror), "rev-list", "--max-parents=0",
                f"{base}..{new}" if base else new).splitlines()
    if roots and (base or git("-C", str(mirror), "ls-remote", "--heads", "origin")):
        for root in roots:
            commit = git("-C", str(mirror), "log", "-1", "--format=%h %s", root)
            warnings.append(f"brings in a separate history: {commit} has no "
                            "parent (another repository?)")

    emails = own_emails(workspace)
    for short, author, author_email, committer, committer_email, _ in commits:
        if not emails:
            break
        if author_email.lower() not in emails:
            warnings.append(f"commit {short} is authored by {author}")
        elif committer_email.lower() not in emails:
            warnings.append(f"commit {short} is committed by {committer}")
    if len(warnings) > 10:
        warnings = warnings[:10] + [f"... {len(warnings) - 10} more commits "
                                    "under another identity"]
    return warnings


def review_files(mirror, base, new):
    """Prints the changed files; returns warnings about the risky ones."""
    raw = git("-C", str(mirror), "diff", "--raw", "-z", "-M", "--no-abbrev",
              base or EMPTY_TREE, new)
    # -z: ":oldmode newmode oldsha newsha status", then one path, or two for
    # renames and copies, each followed by NUL.
    fields = raw.split("\0")
    changes = []
    i = 0
    while i < len(fields) and fields[i].startswith(":"):
        old_mode, new_mode, _, blob, status = fields[i][1:].split()
        count = 2 if status[0] in "RC" else 1
        path = fields[i + count]
        changes.append((status[0], old_mode, new_mode, blob, path))
        i += count + 1

    counts = {}
    for status, *_ in changes:
        counts[status] = counts.get(status, 0) + 1
    names = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed",
             "C": "copied", "T": "type changed"}
    print(f"\n  {len(changes)} file{'s' if len(changes) != 1 else ''} changed: "
          + ", ".join(f"{n} {names.get(s, s)}" for s, n in sorted(counts.items())))
    for status, _, _, _, path in changes[:SHOWN_FILES]:
        print(f"  {status} {path}")
    if len(changes) > SHOWN_FILES:
        print(f"  ... and {len(changes) - SHOWN_FILES} more")

    blobs = [blob for status, _, mode, blob, _ in changes
             if status != "D" and mode.startswith("100")]
    sizes = {}
    if blobs:
        result = subprocess.run(
            ["git", "-C", str(mirror), "cat-file", "--batch-check"],
            input="\n".join(blobs) + "\n", capture_output=True, text=True, check=True)
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) == 3:
                sizes[parts[0]] = int(parts[2])

    warnings = []
    for status, old_mode, new_mode, blob, path in changes:
        if status == "D":
            continue
        name = path.rsplit("/", 1)[-1]
        if CI_FILE.match(path):
            warnings.append(f"CI configuration: {path} (runs on the remote, "
                            "with the repository's secrets)")
        if SANDBOX_FILE.match(path):
            warnings.append(f"changes the sandbox configuration: {path} (read-only "
                            "in the container, but a commit can still change it)")
        if GIT_BEHAVIOUR_FILE.search(path):
            warnings.append(f"changes how git handles the repository: {path}")
        if CREDENTIAL_NAME.match(name):
            warnings.append(f"looks like credentials: {path}")
        if new_mode == "100755" and old_mode != "100755":
            warnings.append(f"new executable: {path}")
        if new_mode == "120000" and old_mode != "120000":
            warnings.append(f"new symbolic link: {path}")
        if new_mode == "160000":
            warnings.append(f"submodule points to another commit: {path}")
        if sizes.get(blob, 0) >= LARGE_FILE_BYTES:
            warnings.append(f"large file ({sizes[blob] // 2**20} MB): {path}")
    return warnings


def main():
    parser = argparse.ArgumentParser(
        description="Pushes the current branch of PROJECT_DIR (default: current "
                    "directory) to origin, only if that is a fast-forward.")
    parser.add_argument("project", nargs="?", default=".", metavar="PROJECT_DIR")
    parser.add_argument("--branch", help="push this branch instead of the checked-out one")
    parser.add_argument("--yes", action="store_true", help="push without asking")
    parser.add_argument("--pause", action="store_true",
                        help="wait for Enter before exiting; for a launcher "
                             "whose window would otherwise close at once")
    args = parser.parse_args()

    workspace = Path(args.project).expanduser().resolve()
    if not (workspace / ".git").exists():
        die(f"Not a git repository: {workspace}")

    # The remote comes from .devcontainer/sandbox.env, which the container
    # cannot write, never from .git/config, which it can (remote.py).
    warnings = []
    env_file = sandbox_env(workspace)
    remote = read_settings(env_file).get(REMOTE_SETTING)
    if not remote:
        origin = recorded_origin(workspace)
        if not origin:
            die(f"No {REMOTE_SETTING} in {env_file}, and no origin in .git/config")
        print(f"{env_file} does not name the project's repository yet.\n"
              f"origin in .git/config is {origin}\n"
              "The container can change .git/config, so check that this is right.")
        if args.yes or input(f"Write {REMOTE_SETTING}={origin} to sandbox.env? "
                             "[y/N] ").strip().lower() not in ("y", "yes"):
            die(f"Aborted. Add {REMOTE_SETTING}=<url> to {env_file}")
        write_remote(env_file, origin)
        remote = origin
    else:
        mismatch = origin_mismatch(workspace, remote)
        if mismatch:
            warnings.append(f"{mismatch}; pushing to the latter")

    # One mirror per project folder, named like start.py's state. It is only
    # a place to push from; its origin always follows sandbox.env.
    name = re.sub(r"[^A-Za-z0-9_-]", "-", workspace.name)
    digest = hashlib.sha256(str(workspace).encode()).hexdigest()[:6]
    mirror = STATE_DIR / "push" / f"{name}-{digest}.git"
    if not mirror.is_dir():
        mirror.parent.mkdir(parents=True, exist_ok=True)
        git("init", "-q", "--bare", str(mirror))
        git("-C", str(mirror), "remote", "add", "origin", remote)
    git("-C", str(mirror), "remote", "set-url", "origin", remote)

    branch = args.branch
    if not branch:
        head = git("ls-remote", "--symref", str(workspace), "HEAD")
        match = re.match(r"ref: refs/heads/(\S+)\tHEAD", head)
        if not match:
            die("The project is not on a branch (detached HEAD), use --branch")
        branch = match.group(1)
    if git("check-ref-format", "--branch", branch, check=False) is None:
        die(f"Invalid branch name: {branch}")

    # The project's commits, into a ref of the mirror's own.
    git("-C", str(mirror), "fetch", "-q", "--no-tags", "--no-write-fetch-head",
        str(workspace), f"+refs/heads/{branch}:refs/project/{branch}")
    new = git("-C", str(mirror), "rev-parse", f"refs/project/{branch}")

    # Where the remote branch is now. Its commit is in the mirror only if the
    # project has it, which is exactly the condition for a fast-forward, so
    # nothing has to be downloaded from the remote.
    listed = git("-C", str(mirror), "ls-remote", "origin", f"refs/heads/{branch}")
    old = listed.split()[0] if listed else None
    if old == new:
        print(f"{branch} is up to date on {remote}")
        return
    if old and not (known_commit(mirror, old) and git(
            "-C", str(mirror), "merge-base", "--is-ancestor", old, new,
            check=False) is not None):
        die(f"{remote} has commits on {branch} that the project does not have.\n"
            "Fetch and merge them in the project first (git pull, also inside "
            "the container), then push again.")

    lfs = git("-C", str(mirror), "grep", "-l", "-e", "filter=lfs", new, "--",
              ".gitattributes", ":(glob)**/.gitattributes", check=False)
    if lfs:
        die("The project uses Git LFS (filter=lfs in .gitattributes). Its files "
            "are uploaded by a pre-push hook, which devcontainer-push does not "
            "run, so the remote would get pointers without content. Push this "
            "project another way.")

    base = old or fork_point(mirror, new)

    # The draft directory is where the container writes its proposals for the
    # protected files; what belongs in the history is the approved file. A
    # commit that carries the draft would put the unreviewed version into the
    # repository, so this looks at every commit of the push, not just at the
    # result: a directory added and removed again in between still travelled.
    drafted = git("-C", str(mirror), "log", "--no-decorate", "--format=%h %s",
                  f"{base}..{new}" if base else new, "--", DRAFT_DIR)
    if drafted:
        listed = "\n".join(f"  {line}" for line in drafted.splitlines()[:10])
        die(f"These commits contain {DRAFT_DIR}/, which holds the container's "
            f"proposals for the protected files:\n{listed}\n\n"
            f"Only the approved files belong in the history. Take the "
            f"directory out of those commits (git rm -r --cached {DRAFT_DIR}, "
            "then amend or rebase), and it stays out by itself afterwards: it "
            "carries a .gitignore of its own.")

    print(f"Push to {remote}\n"
          f"  {branch}: {old[:12] if old else '(new branch)'} -> {new[:12]}")
    warnings += review_commits(mirror, base, new, workspace)
    warnings += review_files(mirror, base, new)
    if warnings:
        print("\nWarnings:")
        print("\n".join(f"  - {line}" for line in warnings))
    if not args.yes:
        if input("\nPush? [y/N] ").strip().lower() not in ("y", "yes"):
            die("Aborted.")
    elif warnings:
        die("Not pushed: there are warnings. Run without --yes to review them.")

    # No "+" and no --force: the remote refuses anything but a fast-forward,
    # even if it moved since the check above.
    sys.stdout.flush()
    result = subprocess.run(["git", "-C", str(mirror), "push", "origin",
                             f"{new}:refs/heads/{branch}"])
    if result.returncode != 0:
        die("Push failed")


if __name__ == "__main__":
    pause = "--pause" in sys.argv and sys.stdin.isatty()
    try:
        main()
    except SystemExit as stop:
        if isinstance(stop.code, str):
            print(stop.code, file=sys.stderr)
        if pause:
            input("\nPress Enter to close this window.")
        raise SystemExit(1 if stop.code else 0)
    except KeyboardInterrupt:
        raise SystemExit(130)
    if pause:
        input("\nPress Enter to close this window.")
