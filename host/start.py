#!/usr/bin/env python3
"""Starts the dev container and opens it in a VS Code-compatible editor over SSH
(e.g. VSCodium with Open Remote - SSH), without forwarding the SSH agent.

Runs on the host, installed once to ~/.local/share/devcontainer-sandbox/ (see
README.md), outside every project, so no container can tamper with it.

Requirements: the project uses the sandbox feature (sshd on port 2222) and
publishes the port on the host (see template/.devcontainer/devcontainer.json).
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from devcontainer_cli import DOCKER, Cli, die, docker_value  # noqa: E402

CONTAINER_SSH_PORT = "2222"
SSH_KEY = Path(os.environ.get("DEVCONTAINER_SSH_KEY")
               or Path.home() / ".ssh/devcontainer_ed25519")
SSH_CA = Path(os.environ.get("DEVCONTAINER_SSH_CA")
              or Path.home() / ".config/devcontainer-sandbox/ssh-ca/ca")
SSH_CONFIG = Path.home() / ".ssh/config"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME")
                 or Path.home() / ".local/state") / "devcontainer-sandbox"
# Warn when the image is older than this: the daily build is not running.
IMAGE_MAX_AGE_HOURS = 48
CERTIFICATE_HOURS = 24

EPILOG = """\
environment:
  DEVCONTAINER_EDITOR   editor command
  DEVCONTAINER_SSH_KEY  private key for the login (default: ~/.ssh/devcontainer_ed25519)
  DEVCONTAINER_SSH_CA   CA key that signs server certificates
                        (default: ~/.config/devcontainer-sandbox/ssh-ca/ca)
  DOCKER_PATH           docker or podman (default: docker; podman needs a local devcontainer CLI)
"""


def load_jsonc(path):
    """Parses JSON with comments and trailing commas (devcontainer.json).

    A regex over the file would pick up commented-out settings, which is why
    the comments are removed properly here.
    """
    text = path.read_text()
    out = []
    i, in_string = 0, False
    while i < len(text):
        char = text[i]
        if in_string:
            out.append(char)
            if char == "\\":
                out.append(text[i + 1])
                i += 2
                continue
            if char == '"':
                in_string = False
            i += 1
        elif char == '"':
            in_string = True
            out.append(char)
            i += 1
        elif text.startswith("//", i):
            newline = text.find("\n", i)
            i = len(text) if newline < 0 else newline
        elif text.startswith("/*", i):
            end = text.find("*/", i)
            i = len(text) if end < 0 else end + 2
        else:
            out.append(char)
            i += 1
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", "".join(out)))


def read_settings(path):
    """Reads .devcontainer/sandbox.env as KEY=VALUE. Parsed, never executed."""
    settings = {}
    if not path.is_file():
        return settings
    for line in path.read_text().splitlines():
        match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)=([^\s#]*)", line)
        if match:
            settings[match.group(1)] = match.group(2)
    return settings


def image_age(image):
    """How old the image is, from 'Created' (RFC 3339 with nanoseconds)."""
    created = docker_value("image", "inspect", "-f", "{{.Created}}", image)
    stamp = re.sub(r"\.\d+", "", created).replace("Z", "+00:00")
    return datetime.now(timezone.utc) - datetime.fromisoformat(stamp)


def split_ssh_config(begin_marker, end_marker):
    """Splits ~/.ssh/config into our managed block and everything else."""
    block, rest, inside = [], [], False
    lines = SSH_CONFIG.read_text().splitlines() if SSH_CONFIG.exists() else []
    for line in lines:
        if line.startswith(begin_marker):
            inside = True
        if inside:
            block.append(line)
            if line == end_marker:
                inside = False
        else:
            rest.append(line)
    return "\n".join(block), "\n".join(rest).strip("\n")


def write_ssh_config(rest, block):
    """Writes the config in place (not mv), to keep symlinks from a dotfiles repo."""
    SSH_CONFIG.parent.mkdir(mode=0o700, exist_ok=True)
    SSH_CONFIG.touch(mode=0o600)
    SSH_CONFIG.chmod(0o600)
    SSH_CONFIG.write_text(f"{rest}\n\n{block}\n" if rest else f"{block}\n")


def sign_server_certificate(settings, container_id, remote_user, name):
    """Short-lived SSH access to a server (README "Server access").

    On every start the container creates a new key pair and the host signs its
    public key with the CA, valid for 24 hours. The private key never leaves
    the container.
    """
    host = settings.get("SERVER_SSH_HOST", "")
    user = settings.get("SERVER_SSH_USER", "")
    port = settings.get("SERVER_SSH_PORT") or "22"
    alias = settings.get("SERVER_SSH_ALIAS") or "server"
    # The values end up in the container's ssh config and the certificate.
    for value in (host, user, port, alias):
        if not re.fullmatch(r"[A-Za-z0-9._-]+", value):
            die(f"Invalid or missing server setting: '{value}'")
    if not SSH_CA.is_file():
        die(f'SSH CA key {SSH_CA} not found, see README "Server access"')

    # Pin the server's host key from the host's known_hosts.
    known_name = host if port == "22" else f"[{host}]:{port}"
    found = subprocess.run(["ssh-keygen", "-F", known_name],
                           capture_output=True, text=True).stdout
    known = "\n".join(line for line in found.splitlines()
                      if line and not line.startswith("#"))
    if not known:
        die(f"No host key for {known_name} in ~/.ssh/known_hosts, "
            f"connect once from the host: ssh -p {port} {host}")

    def in_container(script, stdin=None, capture=False):
        return subprocess.run(
            [DOCKER, "exec", "-i", "-u", remote_user, container_id, "sh", "-c", script],
            input=stdin, text=True, check=True,
            stdout=subprocess.PIPE if capture else None).stdout

    public_key = in_container(
        'umask 077 && mkdir -p ~/.ssh && rm -f ~/.ssh/sandbox_ed25519* && '
        'ssh-keygen -q -t ed25519 -N "" -C sandbox -f ~/.ssh/sandbox_ed25519 && '
        'cat ~/.ssh/sandbox_ed25519.pub', capture=True)

    with tempfile.TemporaryDirectory() as tmp:
        key_pub = Path(tmp) / "key.pub"
        key_pub.write_text(public_key)
        identity = f"devcontainer:{name}:{datetime.now():%Y-%m-%dT%H:%M}"
        # -O clear: no port, agent or X11 forwarding; a terminal is still allowed.
        subprocess.run(
            ["ssh-keygen", "-q", "-s", str(SSH_CA), "-I", identity, "-n", user,
             "-V", f"-5m:+{CERTIFICATE_HOURS}h", "-O", "clear", "-O", "permit-pty",
             str(key_pub)], check=True)
        certificate = (Path(tmp) / "key-cert.pub").read_text()

    in_container("umask 077 && cat > ~/.ssh/sandbox_ed25519-cert.pub", stdin=certificate)
    in_container("umask 077 && cat > ~/.ssh/known_hosts", stdin=known + "\n")
    in_container("umask 077 && cat > ~/.ssh/config", stdin=f"""\
# Managed by devcontainer-start, rewritten on every start.
Host {alias}
    HostName {host}
    Port {port}
    User {user}
    IdentityFile ~/.ssh/sandbox_ed25519
    CertificateFile ~/.ssh/sandbox_ed25519-cert.pub
    IdentitiesOnly yes
    StrictHostKeyChecking yes
""")
    until = datetime.now() + timedelta(hours=CERTIFICATE_HOURS)
    print(f"Server access in the container: ssh {alias} ({user}@{host}), "
          f"valid until {until:%Y-%m-%d %H:%M}")


def grant_device_groups(container_id, remote_user):
    """Gives the user access to passed-through devices (README "GPU").

    "--group-add" only adds the group to the container's own process. An SSH
    login builds its group list from /etc/group inside the container, where
    the host's render group does not exist, so over SSH the GPU would stay
    invisible and OpenGL would fall back to software rendering. The group ids
    are read from the devices themselves, so this needs no configuration.
    """
    devices = docker_value("exec", container_id, "sh", "-c",
                           "stat -c %g /dev/dri/* 2>/dev/null")
    for gid in sorted(set((devices or "").split())):
        # A group with that id may already exist under any name (e.g. video).
        docker_value("exec", "-u", "root", container_id, "sh", "-c",
                     f"getent group {gid} >/dev/null || groupadd -g {gid} host-{gid}; "
                     f"usermod -aG {gid} {remote_user}")


def find_editor(wanted):
    """The editor command, and a check that it can open a remote SSH folder."""
    editor = wanted or next(
        (e for e in ("codium", "code") if shutil.which(e)), None)
    if not editor or not shutil.which(editor):
        die("No editor found, use --editor CMD")
    # Open Remote - SSH (jeanp413.open-remote-ssh), Microsoft Remote - SSH, ...
    extensions = subprocess.run([editor, "--list-extensions"],
                                capture_output=True, text=True).stdout
    if "remote-ssh" not in extensions.lower():
        die(f"No Remote SSH extension in {editor}, e.g.: "
            f"{editor} --install-extension jeanp413.open-remote-ssh")
    return editor


def main():
    parser = argparse.ArgumentParser(
        description="Starts the dev container of PROJECT_DIR (default: current directory).",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project", nargs="?", default=".", metavar="PROJECT_DIR")
    parser.add_argument("--rebuild", action="store_true",
                        help="remove the existing container and create it again")
    parser.add_argument("--no-open", dest="open", action="store_false",
                        help="only start the container, do not launch the editor")
    parser.add_argument("--editor", metavar="CMD",
                        default=os.environ.get("DEVCONTAINER_EDITOR"),
                        help="editor command (default: $DEVCONTAINER_EDITOR, codium or code)")
    parser.add_argument("--yes", action="store_true",
                        help="add the SSH host entry to ~/.ssh/config without asking")
    args = parser.parse_args()

    workspace = Path(args.project).expanduser()
    if not workspace.is_dir():
        die(f"Project directory not found: {args.project}")
    workspace = workspace.resolve()
    config_file = workspace / ".devcontainer/devcontainer.json"
    if not config_file.is_file():
        die(f"No .devcontainer/devcontainer.json in {workspace}")

    cli = Cli(workspace)
    editor = find_editor(args.editor) if args.open else None

    # Unique per workspace, so several projects can run side by side.
    name = re.sub(r"[^A-Za-z0-9_-]", "-", workspace.name)
    digest = hashlib.sha256(str(workspace).encode()).hexdigest()[:6]
    ssh_host = f"devcontainer-{name}-{digest}"

    # Create bind-mount sources on the host. The same script also runs as
    # initializeCommand, but inside the CLI container ssh-keygen may not work.
    script_dir = Path(__file__).resolve().parent
    subprocess.run([sys.executable, str(script_dir / "initialize.py")], check=True)
    if not SSH_KEY.is_file():
        die(f"SSH key {SSH_KEY} not found")

    # Keep the sandbox current: recreate the container when the image changed
    # since the last start (daily build, see build-image.py). Only the project
    # folder and the volumes survive, which is where everything of value lives.
    # Images from a registry are pulled first; local/ images are built here.
    rebuild = args.rebuild
    image = load_jsonc(config_file).get("image")
    state_file = STATE_DIR / f"{name}-{digest}.image"
    image_id = None
    if image:
        if not image.startswith("local/"):
            if docker_value("pull", "-q", image) is None:
                print(f"Warning: cannot pull {image}, using the local copy",
                      file=sys.stderr)
        image_id = docker_value("image", "inspect", "-f", "{{.Id}}", image)
        if not image_id:
            die(f"Image {image} not found; build it first: devcontainer-build-image")
        if state_file.exists() and state_file.read_text().strip() != image_id:
            print(f"New image {image}, recreating the container")
            rebuild = True
        if image_age(image) >= timedelta(hours=IMAGE_MAX_AGE_HOURS):
            print(f"Warning: {image} is older than {IMAGE_MAX_AGE_HOURS} hours, "
                  "is the daily build running? "
                  "(systemctl --user status devcontainer-build-image.timer)",
                  file=sys.stderr)

    up = ["up", "--workspace-folder", str(workspace)]
    if rebuild:
        up.append("--remove-existing-container")
    # The last output line is compact JSON, e.g. {"outcome":"success",
    # "containerId":"...","remoteUser":"vscode","remoteWorkspaceFolder":"..."}
    output = cli.run(*up, capture=True)
    result = json.loads(output.splitlines()[-1]) if output else {}
    if result.get("outcome") != "success":
        die(f"devcontainer up failed: {output}")
    container_id = result["containerId"]
    remote_user = result["remoteUser"]
    remote_folder = result["remoteWorkspaceFolder"]

    if image_id:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(image_id + "\n")
        # A new pull leaves the previous build untagged; drop it once no
        # container uses it any more, otherwise daily builds fill the disk.
        docker_value("image", "prune", "-f")

    grant_device_groups(container_id, remote_user)

    settings = read_settings(workspace / ".devcontainer/sandbox.env")
    if settings.get("SERVER_SSH_HOST"):
        sign_server_certificate(settings, container_id, remote_user, name)

    published = docker_value("port", container_id, f"{CONTAINER_SSH_PORT}/tcp") or ""
    port = published.splitlines()[0].rsplit(":", 1)[-1] if published else ""
    if not port:
        die(f"The container does not publish port {CONTAINER_SSH_PORT} "
            "(created before SSH was added?). Run again with --rebuild.")

    # Managed block in ~/.ssh/config. The port changes whenever the container
    # starts, so the block is rewritten when needed. Host keys change on every
    # rebuild and the port is bound to loopback, so host key checking is off.
    begin_marker = f"# >>> {ssh_host} "
    end_marker = f"# <<< {ssh_host}"
    block = f"""\
{begin_marker}(managed by devcontainer-start for {workspace})
Host {ssh_host}
    HostName 127.0.0.1
    Port {port}
    User {remote_user}
    IdentityFile {SSH_KEY}
    IdentitiesOnly yes
    ForwardAgent no
    ForwardX11 no
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
    LogLevel ERROR
{end_marker}"""

    current, rest = split_ssh_config(begin_marker, end_marker)
    if current != block:
        if not current and not args.yes:
            print(f"This entry will be added to {SSH_CONFIG}:\n\n{block}\n")
            if not sys.stdin.isatty():
                die("Not a terminal, run again with --yes to add it.")
            if input("Add it? [y/N] ").strip().lower() not in ("y", "yes"):
                die("Aborted. Container is running, connect manually: "
                    f"ssh -p {port} -i {SSH_KEY} {remote_user}@127.0.0.1")
        write_ssh_config(rest, block)

    print("Waiting for sshd", end="", flush=True)
    for _ in range(30):
        reachable = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=2", ssh_host, "true"],
            capture_output=True)
        if reachable.returncode == 0:
            break
        print(".", end="", flush=True)
    else:
        print()
        die(f"Cannot connect: ssh {ssh_host}")
    print(f" ok (ssh {ssh_host})")

    if editor:
        # Without SSH_AUTH_SOCK there is no agent to forward, even if all other
        # safeguards failed. Has no effect if the editor is already running.
        environment = {k: v for k, v in os.environ.items() if k != "SSH_AUTH_SOCK"}
        subprocess.Popen(
            [editor, "--folder-uri",
             f"vscode-remote://ssh-remote+{ssh_host}{remote_folder}"],
            env=environment)


if __name__ == "__main__":
    main()
