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
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import STATE_DIR, TOKEN_DIR  # noqa: E402
from devcontainer_cli import (DOCKER, Cli, die,  # noqa: E402
                              docker_value, load_jsonc)
from protected import missing_paths, update_base  # noqa: E402
from deploy_key import (CONTAINER_KEY, CONTAINER_KNOWN_HOSTS,  # noqa: E402
                        KEY_DIR, deploy_repo, key_file)
from remote import (REMOTE_SETTING, github_repo, origin_mismatch,  # noqa: E402
                    read_settings, recorded_origin, sandbox_env)

CONTAINER_SSH_PORT = "2222"
# Reading the model list costs nothing; only the authentication is of
# interest, to tell a mistyped or truncated token from a working one.
CLAUDE_MODELS_URL = "https://api.anthropic.com/v1/models"
# Where tokens are listed and revoked. They carry no name there, only the
# time they were created, which is why that time is printed here.
CLAUDE_TOKEN_PAGE = "https://claude.ai/settings/claude-code"
# A token is around 110 characters. Anything far below that is a copy that
# lost its end, which is what a wrapped terminal line does to one.
TOKEN_MIN_LENGTH = 60
CLAUDE_SETTINGS = "~/.claude/settings.json"
CLAUDE_CREDENTIALS = "~/.claude/.credentials.json"
TOKEN_VARIABLE = "CLAUDE_CODE_OAUTH_TOKEN"
SSH_KEY = Path(os.environ.get("DEVCONTAINER_SSH_KEY")
               or Path.home() / ".ssh/devcontainer_ed25519")
SSH_CA = Path(os.environ.get("DEVCONTAINER_SSH_CA")
              or Path.home() / ".config/devcontainer-sandbox/ssh-ca/ca")
SSH_CONFIG = Path.home() / ".ssh/config"
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


def watched_image(config_file):
    """The image whose updates should recreate the container.

    Usually "image" from devcontainer.json. A project that adds packages of
    its own builds on top of the sandbox image with its own Dockerfile; then
    its FROM line is what has to be watched, because that is what the daily
    build renews.
    """
    config = load_jsonc(config_file)
    if config.get("image"):
        return config["image"]
    build = config.get("build") or {}
    dockerfile = config_file.parent / (build.get("dockerfile") or "Dockerfile")
    if not dockerfile.is_file():
        return None
    for line in dockerfile.read_text().splitlines():
        match = re.match(r"FROM\s+(\S+)", line)
        # A FROM with a build argument cannot be resolved here.
        if match and "$" not in match.group(1):
            return match.group(1)
    return None


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


def in_container(container_id, remote_user, script, stdin=None, capture=False):
    """Runs a shell script in the container as the remote user."""
    return subprocess.run(
        [DOCKER, "exec", "-i", "-u", remote_user, container_id, "sh", "-c", script],
        input=stdin, text=True, check=True,
        stdout=subprocess.PIPE if capture else None).stdout


def gh(*args):
    """Runs the GitHub CLI on the host. Returns stdout, or None on failure."""
    result = subprocess.run(["gh", *args], capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr.strip(), file=sys.stderr)
        return None
    return result.stdout


def ensure_deploy_key(workspace, settings):
    """Registers the project's read-only GitHub deploy key, if it has none yet.

    Runs before initialize.py, which points the container's gitconfig at the
    key as soon as the key file exists. So a key that could not be registered
    is never written; the container then fetches over HTTPS, which works for
    public repositories. See deploy_key.py.
    """
    remote = settings.get(REMOTE_SETTING)
    if not remote:
        if github_repo(recorded_origin(workspace)):
            print(f"Note: to fetch this project's repository with a deploy key, "
                  f"name it in .devcontainer/sandbox.env: "
                  f"{REMOTE_SETTING}={recorded_origin(workspace)}")
        return
    mismatch = origin_mismatch(workspace, remote)
    if mismatch:
        print(f"Warning: {mismatch}. Changed from inside the container?",
              file=sys.stderr)
    repo = deploy_repo(settings)
    if not repo:
        return
    hint = (f"the container fetches {repo} over HTTPS (public repositories only). "
            "GIT_DEPLOY_KEY=no in .devcontainer/sandbox.env stops trying.")
    if not shutil.which("gh"):
        print(f"Warning: no gh on the host to add a deploy key; {hint}",
              file=sys.stderr)
        return

    key = key_file(workspace, repo)
    if key.is_file():
        public = " ".join(key.with_name(key.name + ".pub").read_text().split()[:2])
        listed = gh("api", "--paginate", f"repos/{repo}/keys", "--jq", ".[].key")
        if listed is None:
            print(f"Warning: cannot check the deploy key of {repo}", file=sys.stderr)
            return
        if public in listed.splitlines():
            return
        print(f"The deploy key of {repo} was deleted on GitHub, adding it again")

    with tempfile.TemporaryDirectory() as tmp:
        new_key = Path(tmp) / key.name
        if not key.is_file():
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "",
                            "-C", f"devcontainer-{workspace.name}", "-f", str(new_key)],
                           check=True)
        source = key if key.is_file() else new_key
        public = source.with_name(source.name + ".pub").read_text().strip()
        title = f"devcontainer-sandbox: {workspace.name} on {socket.gethostname()}"
        if gh("api", "-X", "POST", f"repos/{repo}/keys", "-f", f"title={title}",
              "-f", f"key={public}", "-F", "read_only=true") is None:
            print(f"Warning: cannot add a deploy key to {repo} (it needs admin "
                  f"rights on the repository); {hint}", file=sys.stderr)
            return
        if source == new_key:
            KEY_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
            for suffix in ("", ".pub"):
                shutil.move(f"{new_key}{suffix}", f"{key}{suffix}")
    print(f"Added a read-only deploy key to {repo}")


def install_deploy_key(workspace, settings, container_id, remote_user):
    """Copies the deploy key and GitHub's host keys into the container.

    Like the server certificate, they live in the container's ~/.ssh, so this
    runs on every start. Without a key, a copy from earlier is removed.
    """
    repo = deploy_repo(settings)
    if not (repo and key_file(workspace, repo).is_file()):
        in_container(container_id, remote_user,
                     f"rm -f {CONTAINER_KEY} {CONTAINER_KNOWN_HOSTS}")
        return
    # GitHub publishes its host keys in the API, fetched here over HTTPS.
    host_keys = gh("api", "meta", "--jq", ".ssh_keys[]")
    if not host_keys:
        print("Warning: cannot get GitHub's SSH host keys, "
              "the container cannot fetch over SSH", file=sys.stderr)
        return
    known = "".join(f"github.com {line}\n" for line in host_keys.splitlines())
    in_container(container_id, remote_user,
                 f"umask 077 && mkdir -p ~/.ssh && cat > {CONTAINER_KEY}",
                 stdin=key_file(workspace, repo).read_text())
    in_container(container_id, remote_user,
                 f"umask 077 && cat > {CONTAINER_KNOWN_HOSTS}", stdin=known)


def token_accepted(token):
    """Whether Anthropic accepts this token; None if the check did not happen.

    Only 401 counts as a rejection, so a change on the other side can never
    make this throw away a token that works.
    """
    request = urllib.request.Request(CLAUDE_MODELS_URL, headers={
        "authorization": f"Bearer {token}",
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "oauth-2025-04-20"})
    try:
        with urllib.request.urlopen(request, timeout=15):
            return True
    except urllib.error.HTTPError as error:
        return error.code != 401
    except OSError:
        return None


def paste_token():
    """Reads a pasted token, which arrives in more than one line if the
    terminal wrapped it on the way out of setup-token."""
    print("\nPaste the token here, then press Enter on an empty line:")
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if not line.strip():
            break
        lines.append(line)
    return "".join("".join(lines).split())


def mint_claude_token(workspace):
    """Creates this project's Claude Code token with "claude setup-token".

    That command is a browser flow in a full-screen terminal interface, and it
    prints nothing at all when its output is redirected, so a script cannot
    capture the token: it is read off the screen and pasted back here. What is
    left to do here is what goes wrong by hand — the file name, the
    permissions (a shell redirect leaves the token world-readable), and not
    quietly replacing a token that is still valid.
    """
    token_file = TOKEN_DIR / workspace.name
    if not sys.stdin.isatty():
        die("--claude-token asks questions, so it needs a terminal")
    if not shutil.which("claude"):
        die('No claude on the host to create a token with, see README '
            '"Claude Code\'s login"')
    if token_file.is_file():
        print(f"{token_file} already holds a token for this project, created "
              f"{datetime.fromtimestamp(token_file.stat().st_mtime):%Y-%m-%d %H:%M}. "
              f"A new one does not revoke it; that is done at {CLAUDE_TOKEN_PAGE}, "
              "where the time is what tells them apart.")
        if input("Create another one and use that? [y/N] ").strip().lower() \
                not in ("y", "yes"):
            return
    print("\nclaude setup-token opens a browser and prints the token at the end.")
    subprocess.run(["claude", "setup-token"])
    token = paste_token()
    if len(token) < TOKEN_MIN_LENGTH:
        die(f"Only {len(token)} characters, too short for a token; nothing "
            "was written. Copy the whole token, including what a wrapped "
            "line put on the next row.")
    accepted = token_accepted(token)
    if accepted is False:
        die("Anthropic rejects this token, so nothing was written. A copy "
            "that lost its end is the usual reason; the token itself was "
            "created and can be replaced by running this again.")
    if accepted is None:
        print("Warning: could not reach the API to check the token, "
              "writing it unchecked", file=sys.stderr)
    TOKEN_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    token_file.write_text(token + "\n")
    token_file.chmod(0o600)
    print(f"Written to {token_file}. It is listed as of now "
          f"({datetime.now():%Y-%m-%d %H:%M}) at {CLAUDE_TOKEN_PAGE}, which is "
          "also where it is revoked; the tokens carry no names there.")


def install_claude_token(workspace, container_id, remote_user):
    """Gives the container the project's own Claude Code token, if it has one.

    A token from "claude setup-token" can only make model requests, so a
    container holding one cannot reach the account behind it: no Remote
    Control, no claude.ai connectors. A login made inside the container is the
    opposite, an OAuth pair with a refresh token, which is why it is removed
    once a token is configured. The token outranks it anyway, so a login there
    would have no effect (README "Claude Code's login").

    The token lives in ~/.claude/settings.json of the container's own volume,
    which no other project can see. Whatever else that file holds stays.
    """
    token_file = TOKEN_DIR / workspace.name
    token = ""
    if token_file.is_file():
        if token_file.stat().st_mode & 0o077:
            token_file.chmod(0o600)
            print(f"Tightened the permissions of {token_file} to 600")
        token = token_file.read_text().strip()
        # It ends up in a JSON file and in the environment, not in a shell.
        if not re.fullmatch(r"\S+", token):
            die(f"{token_file} does not hold a single token")
        if len(token) < TOKEN_MIN_LENGTH:
            print(f"Warning: the token in {token_file} is only {len(token)} "
                  "characters; a copy that lost its end is the usual reason, "
                  "and Claude Code in the container will ask for a login",
                  file=sys.stderr)

    current = in_container(container_id, remote_user,
                           f"cat {CLAUDE_SETTINGS} 2>/dev/null || true",
                           capture=True)
    if not (token or current.strip()):
        return
    try:
        claude_settings = json.loads(current) if current.strip() else {}
    except json.JSONDecodeError:
        die(f"{CLAUDE_SETTINGS} in the container is not valid JSON; "
            "fix or delete it in the container, it is not overwritten here")
    environment = claude_settings.get("env", {})
    if token:
        environment[TOKEN_VARIABLE] = token
    else:
        environment.pop(TOKEN_VARIABLE, None)
    if environment:
        claude_settings["env"] = environment
    else:
        claude_settings.pop("env", None)
    in_container(container_id, remote_user,
                 f"umask 077 && mkdir -p ~/.claude && cat > {CLAUDE_SETTINGS}",
                 stdin=json.dumps(claude_settings, indent=2) + "\n")
    if not token:
        return
    # The login is a full account credential; the token makes it unnecessary.
    removed = in_container(container_id, remote_user,
                           f"rm -vf {CLAUDE_CREDENTIALS}", capture=True)
    print(f"Claude Code in the container uses {token_file.name} "
          "(model requests only)"
          + (", and the login stored in it was removed" if removed.strip() else ""))


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
    force_command = settings.get("SERVER_SSH_FORCE_COMMAND", "").strip()
    # The values end up in the container's ssh config and the certificate.
    for value in (host, user, port, alias):
        if not re.fullmatch(r"[A-Za-z0-9._-]+", value):
            die(f"Invalid or missing server setting: '{value}'")
    # A command has spaces and slashes, so it cannot go through the check
    # above. It is passed to ssh-keygen as a single argument, never to a
    # shell, so only line breaks and control characters have to be refused.
    if any(char in force_command for char in "\n\r") or any(
            ord(char) < 32 or ord(char) == 127 for char in force_command):
        die("SERVER_SSH_FORCE_COMMAND contains line breaks or control characters")
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

    public_key = in_container(
        container_id, remote_user,
        'umask 077 && mkdir -p ~/.ssh && rm -f ~/.ssh/sandbox_ed25519* && '
        'ssh-keygen -q -t ed25519 -N "" -C sandbox -f ~/.ssh/sandbox_ed25519 && '
        'cat ~/.ssh/sandbox_ed25519.pub', capture=True)

    with tempfile.TemporaryDirectory() as tmp:
        key_pub = Path(tmp) / "key.pub"
        key_pub.write_text(public_key)
        identity = f"devcontainer:{name}:{datetime.now():%Y-%m-%dT%H:%M}"
        # -O clear: no port, agent or X11 forwarding; a terminal is still
        # allowed, unless a forced command makes it pointless. -O clear only
        # drops extensions; force-command is a critical option and survives
        # it, but it is written afterwards to make that obvious.
        options = ["-O", "clear"]
        if force_command:
            options += ["-O", f"force-command={force_command}"]
        else:
            options += ["-O", "permit-pty"]
        subprocess.run(
            ["ssh-keygen", "-q", "-s", str(SSH_CA), "-I", identity, "-n", user,
             "-V", f"-5m:+{CERTIFICATE_HOURS}h", *options, str(key_pub)],
            check=True)
        certificate = (Path(tmp) / "key-cert.pub").read_text()

    in_container(container_id, remote_user,
                 "umask 077 && cat > ~/.ssh/sandbox_ed25519-cert.pub", stdin=certificate)
    in_container(container_id, remote_user,
                 "umask 077 && cat > ~/.ssh/known_hosts", stdin=known + "\n")
    in_container(container_id, remote_user,
                 "umask 077 && cat > ~/.ssh/config", stdin=f"""\
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
    runs = (f"runs '{force_command}'" if force_command else "gives a shell")
    print(f"Server access in the container: ssh {alias} ({user}@{host}) "
          f"{runs}, valid until {until:%Y-%m-%d %H:%M}")


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
    parser.add_argument("--claude-token", action="store_true",
                        help="create this project's Claude Code token first "
                             "(claude setup-token), then start as usual")
    parser.add_argument("--pause-on-error", action="store_true",
                        help="wait for a key press when something went wrong; "
                             "for the file manager entry, whose window would "
                             "otherwise close with the message in it")
    args = parser.parse_args()

    workspace = Path(args.project).expanduser()
    if not workspace.is_dir():
        die(f"Project directory not found: {args.project}")
    workspace = workspace.resolve()
    config_file = workspace / ".devcontainer/devcontainer.json"
    if not config_file.is_file():
        die(f"No .devcontainer/devcontainer.json in {workspace}")

    if args.claude_token:
        mint_claude_token(workspace)

    cli = Cli(workspace)
    editor = find_editor(args.editor) if args.open else None

    # Unique per workspace, so several projects can run side by side.
    name = re.sub(r"[^A-Za-z0-9_-]", "-", workspace.name)
    digest = hashlib.sha256(str(workspace).encode()).hexdigest()[:6]
    ssh_host = f"devcontainer-{name}-{digest}"

    settings = read_settings(sandbox_env(workspace))
    ensure_deploy_key(workspace, settings)

    # A read-only mount whose source is missing would not fail: Docker
    # creates it as a directory owned by root, under the name of the file
    # that should have been there (README "Protected files").
    missing = missing_paths(workspace)
    if missing:
        die("Protected paths named in devcontainer.json but not in the "
            f"project: {', '.join(missing)}. Create them first, or take the "
            "mount out; starting now would leave a directory owned by root "
            "in their place.")

    # What the protected files say now is what a proposal will be measured
    # against, unless one is already open for them (README "Protected files").
    update_base(workspace)

    # Create bind-mount sources on the host. The same script also runs as
    # initializeCommand, but inside the CLI container ssh-keygen may not work.
    script_dir = Path(__file__).resolve().parent
    subprocess.run([sys.executable, str(script_dir / "initialize.py"),
                    str(workspace)], check=True)
    if not SSH_KEY.is_file():
        die(f"SSH key {SSH_KEY} not found")

    # Keep the sandbox current: recreate the container when the image changed
    # since the last start (daily build, see build-image.py). Only the project
    # folder and the volumes survive, which is where everything of value lives.
    # Images from a registry are pulled first; local/ images are built here.
    rebuild = args.rebuild
    image = watched_image(config_file)
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

    install_deploy_key(workspace, settings, container_id, remote_user)
    install_claude_token(workspace, container_id, remote_user)
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
        # start_new_session: started from the file manager, this script runs in
        # a terminal window that closes the moment it ends, which would take
        # the editor down with it (same process group). Its output goes
        # nowhere for the same reason.
        subprocess.Popen(
            [editor, "--folder-uri",
             f"vscode-remote://ssh-remote+{ssh_host}{remote_folder}"],
            env=environment, start_new_session=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    try:
        main()
    except SystemExit as stop:
        # die() exits with its message as the code; print it ourselves, so the
        # window started from the file manager can be held open afterwards.
        if isinstance(stop.code, str):
            print(stop.code, file=sys.stderr)
        if stop.code and "--pause-on-error" in sys.argv and sys.stdin.isatty():
            input("\nPress Enter to close this window.")
        raise SystemExit(1 if stop.code else 0)
