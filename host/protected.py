"""Files a project lets its container read but not change (README "Protected files").

The project mounts them read-only in .devcontainer/devcontainer.json, which is
read-only in the container as well, so what is protected is not in the
container's reach. The container writes its proposals into protected_draft/
next to them, and devcontainer-approve copies them over on the host once the
diff has been read.

The draft holds proposals and nothing else. It is never filled with copies of
the protected files: what lies in it is what somebody is being asked to
accept, and a directory full of unchanged copies would bury that. The
container copies a file in itself when it wants to change one, and removing
something is asked for with a marker, never by leaving a file out.

The state in ~/.local/state/devcontainer-sandbox/ says what the protected
files looked like the last time nothing was proposed for them. A file with no
open proposal follows the host freely; while a proposal is open, a change on
the host is a collision, and that is exactly what this makes visible.

Imported by start.py, which records that state, and by approve.py.
"""

import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import STATE_DIR  # noqa: E402
from devcontainer_cli import die, load_jsonc  # noqa: E402

_warned: set = set()


def warn_once(message: str) -> None:
    """Says it once per run: these paths are looked at more than once."""
    if message not in _warned:
        _warned.add(message)
        print(f"Warning: {message}", file=sys.stderr)


DRAFT_DIR = "protected_draft"
# How a draft asks for something to be removed. Absence never means deletion:
# a draft that lost a file through a mishap would otherwise propose throwing
# the original away, and nobody would see it as anything but a missing line.
DELETE_SUFFIX = ".delete"
SANDBOX_DIR = ".devcontainer"
WORKSPACE_PREFIX = "${localWorkspaceFolder}/"
CONTAINER_PREFIX = "${containerWorkspaceFolder}/"


def mount_fields(mount) -> tuple[str, str, bool]:
    """(source, type, readonly) of a devcontainer.json mount, in either form."""
    if isinstance(mount, dict):
        readonly = bool(mount.get("readonly") or mount.get("readOnly"))
        return mount.get("source", ""), mount.get("type", "bind"), readonly
    parts = [part.strip() for part in str(mount).split(",")]
    fields = dict(part.split("=", 1) for part in parts if "=" in part)
    readonly = ("readonly" in parts
                or fields.get("readonly", "").lower() in ("true", "1"))
    return fields.get("source", ""), fields.get("type", "bind"), readonly


def target_of(mount) -> str:
    """Where a mount lands in the container, in either form."""
    if isinstance(mount, dict):
        return mount.get("target") or mount.get("destination", "")
    fields = dict(part.strip().split("=", 1)
                  for part in str(mount).split(",") if "=" in part)
    return fields.get("target") or fields.get("destination", "")


def relative_path(source: str, prefix: str) -> str | None:
    """The path a mount names below prefix, plainly written, or None.

    None is not a complaint. A read-only mount of a neighbouring project
    (${localWorkspaceFolder}/../other) is an ordinary thing to want, it is
    simply not one of this project's protected paths, and nothing here writes
    to it. Only a path that means to be inside and is written in a way nobody
    can check at a glance is worth saying something about.
    """
    if not source.startswith(prefix):
        return None
    rest = source[len(prefix):]
    parts = [part for part in rest.split("/") if part not in ("", ".")]
    if not parts or ".." in parts:
        return None            # the folder itself, or somewhere outside it
    if rest != "/".join(parts):
        warn_once(f"mount of {source} in devcontainer.json is not counted as "
                  "a protected path: write it plainly, as name or name/inside,"
                  " without doubled or trailing slashes")
        return None
    return "/".join(parts)


def protected_paths(workspace: Path) -> list[str]:
    """The project's read-only paths, relative to the workspace.

    Taken from the mounts themselves, which is what actually holds in the
    container, so there is no second list that could say something else.
    .devcontainer/ is one of them: the container may propose changes to the
    sandbox it runs in, it just cannot make them. Reading those proposals
    deserves more attention than the rest, which is why approve.py says so
    loudly. Only the draft directory itself is left out; a draft of a draft
    means nothing.
    """
    config_file = workspace / SANDBOX_DIR / "devcontainer.json"
    if not config_file.is_file():
        return []
    paths = []
    for mount in load_jsonc(config_file).get("mounts") or []:
        source, kind, readonly = mount_fields(mount)
        if not (readonly and kind == "bind"):
            continue
        relative = relative_path(source, WORKSPACE_PREFIX)
        if relative is None or relative.split("/")[0] == DRAFT_DIR:
            continue
        # The same path on both sides, or the claim does not hold: a file
        # mounted read-only somewhere else leaves the one in the project
        # writable, and approving changes to it would protect nothing. Such
        # a mount is simply not part of this; it is not an error.
        if relative_path(target_of(mount), CONTAINER_PREFIX) != relative:
            continue
        paths.append(relative)
    return sorted(dict.fromkeys(paths))


def missing_paths(workspace: Path) -> list[str]:
    """Protected paths that do not exist on the host.

    Docker creates a missing bind source by itself, as a directory owned by
    root - so a mount for a file that is not there yet leaves a directory
    with the file's name, which takes root to remove again.
    """
    return [relative for relative in protected_paths(workspace)
            if not (workspace / relative).exists()]


def state_file(workspace: Path) -> Path:
    """Named like the other per-project state (start.py)."""
    name = re.sub(r"[^A-Za-z0-9_-]", "-", workspace.name)
    digest = hashlib.sha256(str(workspace).encode()).hexdigest()[:6]
    return STATE_DIR / "protected" / f"{name}-{digest}.json"


def read_state(workspace: Path) -> dict:
    path = state_file(workspace)
    return json.loads(path.read_text()) if path.is_file() else {}


def write_state(workspace: Path, state: dict) -> None:
    path = state_file(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def tree_state(root: Path) -> dict[str, list]:
    """{path inside root: [sha256, mode]}, for a single file under "".

    Symbolic links and everything that is not a plain file or a directory are
    refused instead of followed: a draft comes out of the container, and a
    link pointing out of the tree would write wherever it points. Empty
    directories are not carried over, since there is nothing to copy.
    """
    if root.is_symlink():
        die(f"{root} is a symbolic link; protected paths and drafts must not be")
    if root.is_file():
        return {"": [hashlib.sha256(root.read_bytes()).hexdigest(),
                     root.stat().st_mode & 0o7777]}
    state = {}
    for path in sorted(root.rglob("*")) if root.is_dir() else []:
        if path.is_symlink():
            die(f"{path} is a symbolic link, which is not copied")
        if path.is_dir():
            continue
        if not path.is_file():
            die(f"{path} is neither a plain file nor a directory")
        state[str(path.relative_to(root))] = [
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mode & 0o7777]
    return state


def deletions(marker: str, current: dict) -> list[str]:
    """What a NAME.delete marker asks to remove, as paths inside the protected
    path. A marker for a directory names every file under it, one by one:
    deleting a tree is something to see, not to read about."""
    target = marker[:-len(DELETE_SUFFIX)]
    if not target:
        # A bare ".delete" would ask for the protected path itself, which is
        # a mount source: without it the mount has nothing to point at.
        return []
    if target in current:
        return [target]
    return sorted(inside for inside in current
                  if inside.startswith(target + "/"))


def spoken_for(drafted: dict, current: dict, markers_only: bool = False) -> set:
    """The paths a draft says something about: the files it holds, and what
    its deletion markers name. With markers_only, just the latter."""
    covered = set() if markers_only else {
        inside for inside in drafted if not inside.endswith(DELETE_SUFFIX)}
    for marker in drafted:
        if marker.endswith(DELETE_SUFFIX):
            covered.update(deletions(marker, current))
            covered.add(marker[:-len(DELETE_SUFFIX)])
    return covered


def ignore_draft(workspace: Path) -> None:
    """Keeps the draft directory out of git, without touching the project's
    own .gitignore: a .gitignore of its own, ignoring everything including
    itself. What belongs in a project's history is the approved file, never
    the proposal; devcontainer-push refuses commits that carry the draft."""
    marker = workspace / DRAFT_DIR / ".gitignore"
    if not marker.is_file():
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("# Written by devcontainer-sandbox: proposals for\n"
                          "# protected files are not part of the history.\n*\n")


def reset_draft(workspace: Path) -> None:
    """Empties the draft, apart from the .gitignore that keeps it out of git.

    After an approval nothing in it is pending any more, and what is not
    pending has no business being there.
    """
    root = workspace / DRAFT_DIR
    if root.is_dir():
        for entry in sorted(root.iterdir()):
            if entry.name == ".gitignore":
                continue
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
    write_state(workspace, {})
    update_base(workspace)


def update_base(workspace: Path) -> None:
    """Records what the protected files say where nothing is proposed.

    A file the draft says nothing about is in agreement with the host by
    definition, so whatever it says now is what a later proposal will be
    measured against. So is a draft file that says the same as the protected
    one: identical is not a proposal, and it is how somebody ends a collision
    they have decided by hand. Only a file with something open - different
    content, or a marker asking for it to go - keeps the state it had, which
    is what makes a change on the host in the meantime visible at all.
    """
    paths = protected_paths(workspace)
    if not paths:
        return
    ignore_draft(workspace)
    approved = read_state(workspace)
    for relative in paths:
        source = workspace / relative
        draft = workspace / DRAFT_DIR / relative
        current = tree_state(source) if source.exists() else {}
        drafted = tree_state(draft) if draft.exists() else {}
        was = dict(approved.get(relative, {}))
        marked = spoken_for(drafted, current, markers_only=True)
        for inside in set(current) | set(was):
            if inside in marked:
                continue                       # its removal is proposed
            if inside in drafted and drafted[inside] != current.get(inside):
                continue                       # a change to it is proposed
            if inside in current:
                was[inside] = current[inside]
            else:
                was.pop(inside, None)
        approved[relative] = was
    write_state(workspace, approved)
