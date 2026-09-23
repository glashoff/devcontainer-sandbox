"""Files a project lets its container read but not change (README "Protected files").

The project mounts them read-only in .devcontainer/devcontainer.json, which is
read-only in the container as well, so what is protected is not in the
container's reach. The container writes its proposals into protected_draft/
next to them, and devcontainer-approve copies them over on the host once the
diff has been read.

The last approved state is kept in ~/.local/state/devcontainer-sandbox/, as
hashes. Without it a change made on the host and a change made in the
container cannot be told apart, and approving would silently undo the host's
work. With it, a draft that nobody touched is simply refreshed on every start,
so a file missing from a draft directory means "delete this" and not "I only
wrote one file".

Imported by start.py, which refreshes the untouched drafts, and by approve.py.
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

DRAFT_DIR = "protected_draft"
SANDBOX_DIR = ".devcontainer"
WORKSPACE_PREFIX = "${localWorkspaceFolder}/"


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
        if not (readonly and kind == "bind"
                and source.startswith(WORKSPACE_PREFIX)):
            continue
        relative = source[len(WORKSPACE_PREFIX):].strip("/")
        if relative and relative.split("/")[0] != DRAFT_DIR:
            paths.append(relative)
    return sorted(dict.fromkeys(paths))


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


def copy_tree(source: Path, target: Path) -> None:
    """Replaces target with a copy of source. Only for writing drafts."""
    tree_state(source)  # refuses links and anything else, before deleting
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target, symlinks=False)
    else:
        shutil.copyfile(source, target)
        shutil.copymode(source, target)


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


def sync_draft(workspace: Path) -> list[str]:
    """Brings the draft files nobody edited up to date with the protected ones.

    File by file, not whole trees: one file the container is still working on
    must not keep the rest of the draft in the past, or it would propose
    undoing what the host did in the meantime. A draft file that differs from
    the last approved state is the container's own work and stays untouched.
    """
    approved = read_state(workspace)
    refreshed = []
    if protected_paths(workspace):
        ignore_draft(workspace)
    for relative in protected_paths(workspace):
        source = workspace / relative
        draft = workspace / DRAFT_DIR / relative
        if not source.exists():
            continue
        current = tree_state(source)
        if not draft.exists():
            copy_tree(source, draft)
            approved[relative] = current
            refreshed.append(relative)
            continue
        have = tree_state(draft)
        was = dict(approved.get(relative, {}))
        touched = False
        for inside in sorted(set(current) | set(have) | set(was)):
            if have.get(inside) != was.get(inside):
                continue                       # the container's own work
            if have.get(inside) == current.get(inside):
                continue
            here = (draft / inside) if inside else draft
            there = (source / inside) if inside else source
            if inside in current:
                here.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(there, here)
                shutil.copymode(there, here)
                was[inside] = current[inside]
            else:
                here.unlink()
                was.pop(inside, None)
            touched = True
        approved[relative] = was
        if touched:
            refreshed.append(relative)
    write_state(workspace, approved)
    return refreshed
