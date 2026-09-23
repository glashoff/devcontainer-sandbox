#!/usr/bin/env python3
"""Applies a container's proposals for a project's protected files.

Runs on the host. The container can write its proposals into protected_draft/
but not into the protected files themselves (README "Protected files"); this
command shows what would change, asks, and copies it over.

Nothing from the project is executed, and nothing is followed: a draft that
contains a symbolic link is refused rather than copied, so a link out of the
tree cannot make this write somewhere else.

There is no --yes. The whole point of the command is that somebody read the
diff; a protected file that is changed without that is not protected.
"""

import argparse
import difflib
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from devcontainer_cli import die  # noqa: E402
from protected import (DELETE_SUFFIX, DRAFT_DIR, deletions,  # noqa: E402
                       protected_paths, read_state, reset_draft, tree_state,
                       update_base)

# A protected file is configuration, a rule, a script. Anything of this size
# is worth a second look before it is copied over.
LARGE_FILE_BYTES = 1024 * 1024
DIFF_LINES = 120
SANDBOX_DIR = ".devcontainer"
# Keys in devcontainer.json that decide what the sandbox is worth. A proposal
# that touches one of them is not a configuration change like any other.
SANDBOX_KEYS = ("privileged", "capAdd", "securityOpt", "runArgs", "mounts",
                "features", "remoteUser", "containerEnv", "initializeCommand",
                "onCreateCommand", "updateContentCommand", "postCreateCommand",
                "postStartCommand", "postAttachCommand")


def text(path):
    """The file's lines, or None if it is not text."""
    try:
        return path.read_text().splitlines(keepends=True)
    except (UnicodeDecodeError, OSError):
        return None


def show_diff(old, new, label):
    """Prints a unified diff, shortened. A missing side is an empty file, so
    that a new file is read as added lines and a deleted one as removed."""
    old_lines = [] if old is None else text(old)
    new_lines = [] if new is None else text(new)
    if old_lines is None or new_lines is None:
        here = new if new is not None else old
        print(f"  (not text, {here.stat().st_size} bytes)")
        return
    lines = list(difflib.unified_diff(old_lines, new_lines,
                                      f"a/{label}", f"b/{label}"))
    for line in lines[:DIFF_LINES]:
        print("  " + line.rstrip("\n"))
    if len(lines) > DIFF_LINES:
        print(f"  ... and {len(lines) - DIFF_LINES} more diff lines")


def collect(workspace, relative, approved):
    """Compares one protected path with its draft.

    Returns (changes, conflicts, stray). A change is (inside, kind, mode),
    where "inside" is the path within the protected path ("" for a single
    file). "stray" are deletion markers with nothing to delete.

    A file missing from the draft is not a deletion; only a NAME.delete
    marker is. A draft that lost a file through a mishap would otherwise
    propose throwing the original away, and it would read like nothing.
    """
    source = workspace / relative
    draft = workspace / DRAFT_DIR / relative
    if not draft.exists():
        return [], [], []
    current = tree_state(source) if source.exists() else {}
    drafted = tree_state(draft)
    proposed = {inside: state for inside, state in drafted.items()
                if not inside.endswith(DELETE_SUFFIX)}
    was = approved.get(relative, {})
    changes, conflicts, stray = [], [], []

    removing = {}
    for marker in sorted(inside for inside in drafted
                         if inside.endswith(DELETE_SUFFIX)):
        targets = deletions(marker, current)
        if not targets:
            stray.append(marker)
        for inside in targets:
            removing[inside] = marker

    for inside in sorted(set(proposed) | set(removing)):
        if inside in removing:
            # Removing something the host changed since the last approval is
            # the same collision as two edits, and is decided the same way.
            if current.get(inside) != was.get(inside):
                conflicts.append(inside)
            else:
                changes.append((inside, "deleted", current[inside][1]))
            continue
        if current.get(inside) == proposed.get(inside):
            continue
        # Only what the container changed against the last approved state is
        # a proposal; a draft it left alone must not undo what the host did.
        if proposed.get(inside) == was.get(inside):
            continue
        if current.get(inside) != was.get(inside):
            conflicts.append(inside)
            continue
        changes.append((inside, "new" if inside not in current else "changed",
                        proposed[inside][1]))
    return changes, conflicts, stray


def marker_reason(draft, inside):
    """Whatever the marker that asks for this deletion says, if anything."""
    markers = sorted(draft.rglob("*" + DELETE_SUFFIX)) if draft.is_dir() else []
    for marker in markers:
        target = str(marker.relative_to(draft))[:-len(DELETE_SUFFIX)]
        if target == inside or inside.startswith(target + "/"):
            return " ".join(marker.read_text().split())[:80]
    return ""


def warn_about(workspace, relative, changes, approved):
    """What a container might slip past somebody reading a diff."""
    draft = workspace / DRAFT_DIR / relative
    warnings = []
    for inside, kind, mode in changes:
        shown = f"{relative}/{inside}" if inside else relative
        file = (draft / inside) if inside else draft
        if shown.split("/")[0] == SANDBOX_DIR:
            keys = [key for key in SANDBOX_KEYS
                    if kind != "deleted" and file.is_file()
                    and key in (text(file) and "".join(text(file)) or "")]
            warnings.append(
                f"changes the sandbox configuration itself: {shown}"
                + (f", and it names {', '.join(keys)}" if keys else "")
                + ' (README "Rules when changing it")')
        if kind == "deleted":
            reason = marker_reason(draft, inside)
            warnings.append(f"deletes {shown}"
                            + (f" (the marker says: {reason})" if reason else ""))
            continue
        before = approved.get(relative, {}).get(inside)
        if mode & 0o111 and not (before and before[1] & 0o111):
            warnings.append(f"{shown} becomes executable")
        if before and before[1] != mode:
            warnings.append(f"{shown} changes mode to {mode:o}")
        if file.is_file() and file.stat().st_size >= LARGE_FILE_BYTES:
            warnings.append(f"{shown} is {file.stat().st_size // 1024} KB")
    return warnings


def clear_markers(source, draft):
    """Removes the deletion markers that have done their work, and the
    directories they emptied. A marker left behind would ask for the same
    thing again on the next run, with nothing left to remove."""
    if not draft.is_dir():
        return
    for marker in sorted(draft.rglob("*" + DELETE_SUFFIX)):
        target = source / str(marker.relative_to(draft))[:-len(DELETE_SUFFIX)]
        if target.is_dir() and not any(p.is_file() for p in target.rglob("*")):
            shutil.rmtree(target)
        if not target.exists():
            marker.unlink()


def apply(workspace, relative, changes):
    """Copies the draft over the protected path, file by file."""
    source = workspace / relative
    draft = workspace / DRAFT_DIR / relative
    root = source.parent.resolve()
    for inside, kind, _ in changes:
        target = (source / inside) if inside else source
        origin = (draft / inside) if inside else draft
        # The path was built here, not read from the draft; this is the
        # check that it stayed inside the project all the same.
        if not str(target.resolve().parent).startswith(str(root)):
            die(f"{target} would land outside {root}, nothing further copied")
        if kind == "deleted":
            target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, target)
        shutil.copymode(origin, target)
    clear_markers(source, draft)


def main():
    parser = argparse.ArgumentParser(
        description="Applies what PROJECT_DIR's container proposed for its "
                    "protected files (default: current directory).")
    parser.add_argument("project", nargs="?", default=".", metavar="PROJECT_DIR")
    parser.add_argument("--pause", action="store_true",
                        help="wait for Enter before exiting; for a launcher "
                             "whose window would otherwise close at once")
    args = parser.parse_args()

    workspace = Path(args.project).expanduser()
    if not workspace.is_dir():
        die(f"Project directory not found: {args.project}")
    workspace = workspace.resolve()
    paths = protected_paths(workspace)
    if not paths:
        die(f"{workspace} has no protected paths: no read-only bind mount of "
            'its own folder in .devcontainer/devcontainer.json (README '
            '"Protected files")')

    # Files nothing is proposed for follow the host; the rest keeps the state
    # a proposal would be measured against.
    update_base(workspace)
    approved = read_state(workspace)
    everything, conflicting, strays = {}, {}, {}
    for relative in paths:
        changes, conflicts, stray = collect(workspace, relative, approved)
        if changes:
            everything[relative] = changes
        if conflicts:
            conflicting[relative] = conflicts
        if stray:
            strays[relative] = stray

    if strays:
        print("Deletion markers with nothing to delete:\n")
        for relative, markers in strays.items():
            for marker in markers:
                print(f"  {DRAFT_DIR}/{relative}/{marker}")
        die(f"\nA marker is named NAME{DELETE_SUFFIX} and sits beside where "
            "the file or directory it removes would be in the draft. One that "
            "points at nothing is a mistake, not an empty change: correct it "
            "or take it out, then run this again.")
    if conflicting:
        print("Changed on the host and in the draft, which cannot be decided "
              "here:\n")
        for relative, conflicts in conflicting.items():
            for inside in conflicts:
                print(f"  {relative}/{inside}" if inside else f"  {relative}")
        die(f"\nDecide it by hand: for a change, put what should stand into "
            f"{workspace}/{DRAFT_DIR}; for a deletion, take the marker out if "
            "what the host wrote should stay. Then run this again.")
    if not everything:
        print(f"Nothing to approve in {workspace}")
        return

    warnings = []
    for relative, changes in everything.items():
        print(f"\n{relative}")
        for inside, kind, _ in changes:
            shown = f"  {kind:>7}  {inside}" if inside else f"  {kind:>7}"
            print(shown)
        for inside, kind, _ in changes:
            label = f"{relative}/{inside}" if inside else relative
            print(f"\n--- {label} ({kind})")
            old = (workspace / relative / inside) if inside else workspace / relative
            new = (workspace / DRAFT_DIR / relative / inside) if inside \
                else workspace / DRAFT_DIR / relative
            show_diff(old if old.exists() else None,
                      new if new.exists() else None, label)
        warnings += warn_about(workspace, relative, changes, approved)

    if warnings:
        print("\nWarnings:")
        for line in warnings:
            print(f"  - {line}")
    if input("\nApply this to the protected files? [y/N] ").strip().lower() \
            not in ("y", "yes"):
        die("Aborted, nothing was changed.")

    for relative, changes in everything.items():
        apply(workspace, relative, changes)
    # Nothing in the draft is pending any more, so it starts over from what
    # the protected files now say.
    reset_draft(workspace)
    print(f"Applied in {workspace}, and {DRAFT_DIR}/ starts again from the "
          "files as they are now. The container sees the change when it reads "
          "them again; no restart is needed.")


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
