"""Whether a project's container is in use, judged from its processes.

A container is in use while an SSH session is open in it (the editor, or
someone logged in) or a Claude Code process runs in it, whoever started it:
the voice app runs Claude detached, so it keeps working with no connection at
all. Imported by idle-stop.py and start.py.
"""

from devcontainer_cli import docker_value

# The feature every sandbox container is built with, as the devcontainer CLI
# records it in the container's metadata label.
SANDBOX_FEATURE = '"id":"./sandbox"'


def sandbox_containers():
    """{container id: project folder} of the running sandbox containers.

    Containers of other dev container setups are none of our business.
    """
    listed = docker_value(
        "ps", "--filter", "label=devcontainer.local_folder",
        "--format", '{{.ID}}\t{{.Label "devcontainer.local_folder"}}') or ""
    containers = {}
    for line in listed.splitlines():
        container_id, _, folder = line.partition("\t")
        metadata = docker_value("inspect", "-f",
                                '{{index .Config.Labels "devcontainer.metadata"}}',
                                container_id) or ""
        if SANDBOX_FEATURE in metadata.replace(" ", ""):
            containers[container_id] = folder
    return containers


def container_of(workspace):
    """The id of the running container of a project folder, or None."""
    listed = docker_value("ps", "-q", "--filter",
                          f"label=devcontainer.local_folder={workspace}")
    return listed.splitlines()[0] if listed else None


def processes(container_id):
    """(name, command line) of every process, or None if that failed."""
    listed = docker_value("exec", container_id, "ps", "-eo", "comm=,args=")
    if listed is None:
        return None
    found = []
    for line in listed.splitlines():
        name, _, command = line.strip().partition(" ")
        if name:
            found.append((name, command.strip()))
    return found


def is_claude(name):
    # The native binary is claude.exe; a name is cut at 15 characters.
    return name in ("claude", "claude.exe")


def is_ssh_session(name, command):
    # Older sshd names its session processes "sshd: user@pts/0" and the like,
    # newer ones run them as sshd-session. The listener alone is no session.
    if name == "sshd-session":
        return True
    return command.startswith("sshd: ") and "[listener]" not in command


def claude_running(container_id):
    """Whether Claude Code runs in the container; None if that is unknown."""
    found = processes(container_id)
    if found is None:
        return None
    return any(is_claude(name) for name, _ in found)


def activity(container_id):
    """What keeps the container in use, as a short text; "" if it is idle.

    None if its processes could not be read, which never counts as idle.
    """
    found = processes(container_id)
    if found is None:
        return None
    reasons = []
    if any(is_ssh_session(name, command) for name, command in found):
        reasons.append("SSH session")
    if any(is_claude(name) for name, _ in found):
        reasons.append("Claude Code")
    return ", ".join(reasons)
