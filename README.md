# Base dev container

One sandbox image for all my projects, built **on this host** every day:
`local/devcontainer-sandbox`. Every project uses that image and only adds
project-only tools if needed, so the tools are stored once on disk, not once per
project. No registry, no GitHub Actions.

## What the sandbox guarantees

The agent gets internet access and write access to its project, nothing else
from the host.

| Inside the container | Status |
|---|---|
| Internet (HTTPS, DNS) | allowed |
| Local network (`192.168.0.0/16`, `10.0.0.0/8`, `172.16.0.0/12`, link-local, CGNAT, multicast), Docker host, all IPv6 | blocked |
| Inbound connections | blocked, except sshd on port 2222 (published on a random port on host `127.0.0.1` only, key login as the remote user) |
| `sudo` / becoming root | blocked |
| Host home directory, `~/.ssh`, host `~/.claude` | not mounted (except `~/.claude/CLAUDE.md` and `~/.ssh/devcontainer_ed25519.pub`, read-only) |
| Host SSH agent | not forwarded (refused by sshd, removed on attach if it gets in anyway) |
| Host git credentials | not forwarded; containers cannot reach remote repositories |
| git identity (name, email) | passed through read-only, so commits work ([Host setup](#host-setup)) |
| `.devcontainer/` of the project | read-only |
| Project folder (`/workspaces/<name>`) | read-write |
| Host desktop (Wayland), GPU | not available, unless enabled per project ([Wayland](#wayland), [GPU](#gpu)) |
| Server via SSH | not available, unless enabled per project ([Server access](#server-access)) |

## Tools in the image

- Node LTS (nvm), Claude Code, GitHub CLI, git
- Python (Debian's)
- C/C++: gcc (`build-essential`), clang, clangd, clang-format, clang-tidy,
  LLVM, lld, gdb, cmake, ninja
- Rust: rustup with the current stable toolchain, rustfmt, clippy
- System libraries for browsers started by Playwright ([Browsers](#browsers))
- Wayland client libraries, Mesa (OpenGL/Vulkan, software rendering without GPU)

Per project, in Docker volumes that survive rebuilds: the Claude Code login
(`claude-code-config-<id>`) and VSCodium's remote server with its extensions
(`vscodium-server-<id>`).

## Layout

| Path | Purpose |
|---|---|
| [image/.devcontainer/](image/.devcontainer/) | the image: [Dockerfile](image/.devcontainer/Dockerfile) (Debian packages), [devcontainer.json](image/.devcontainer/devcontainer.json) (features) |
| [image/.devcontainer/sandbox/](image/.devcontainer/sandbox/) | the sandbox feature: firewall entrypoint, sshd hardening, sudo removal, agent check, volumes |
| [image/.devcontainer/playwright-deps/](image/.devcontainer/playwright-deps/) | feature: browser system libraries |
| [image/refresh.Dockerfile](image/refresh.Dockerfile) | daily update layer |
| [image/claude-sandbox.md](image/claude-sandbox.md) | what Claude Code in the container knows about the sandbox and its tools; installed as `/etc/claude-code/CLAUDE.md` |
| [host/setup.py](host/setup.py) | links the scripts, creates the commands and installs the systemd units on this host |
| [host/setup-ca.py](host/setup-ca.py) | creates the SSH certificate authority and configures a server |
| [host/config.py](host/config.py), [config.example](host/config.example) | the local configuration in `~/.config/devcontainer-sandbox/config` |
| [host/build-image.py](host/build-image.py) | builds the image; `devcontainer-build-image` |
| [host/devcontainer-build-image.timer](host/devcontainer-build-image.timer) | runs the build daily |
| [host/build-finished.py](host/build-finished.py) | desktop notification and mail when a build finished |
| [host/start.py](host/start.py) | starts a project's container and opens the editor over SSH; `devcontainer-start` |
| [host/initialize.py](host/initialize.py) | creates the bind-mount sources on the host (`CLAUDE.md`, SSH key) |
| [host/devcontainer_cli.py](host/devcontainer_cli.py) | runs the devcontainer CLI (shared by the scripts) |
| [template/.devcontainer/](template/.devcontainer/) | starting point for a project: `devcontainer.json`, `sandbox.env` |

The sandbox settings (entrypoint, `NET_ADMIN`, `no-new-privileges`, volumes,
agent check) are stored in the image's metadata and apply to every project
automatically. What an image cannot carry stays in each project's
`devcontainer.json`: `runArgs`, `initializeCommand` and the bind mounts from the
host.

## How updates work

The build has two stages:

1. **Base** (`local/devcontainer-sandbox-base`): everything in
   `image/.devcontainer/`, built with the Docker cache. Its layers only change
   when the definition changes. **Once a week** (or with `--full`) it is rebuilt
   without cache, which updates Rust, the features and the Debian base image.
2. **Updates** ([refresh.Dockerfile](image/refresh.Dockerfile)): `apt-get
   upgrade`, the current Node LTS and the newest Claude Code, rebuilt **every
   day** on top. On an ordinary day this small layer is the only new one.

Docker reuses a cached layer without running its command again, so a cached
build keeps the versions its layers were built with; only `--no-cache` fetches
new ones, and then every layer is new. That is why the daily layer sits on the
weekly base instead of on yesterday's image: it is thrown away and rebuilt each
day, so the image stays "base plus one layer" instead of growing a layer per
day. Tools that must update daily therefore belong in
[refresh.Dockerfile](image/refresh.Dockerfile), not in a feature.

The timer runs the build daily; if the laptop was off or suspended, it catches
up afterwards (it never wakes the laptop). `devcontainer-start` then sees the
new image and recreates the container. Only the project folder and the volumes
survive, which is where everything of value lives; tools installed by hand
inside the container are gone. A container that keeps running for days is not
updated; start it again with `devcontainer-start` now and then.
`devcontainer-start` warns if the image is older than 48 hours.

Before each build the current image is tagged `local/devcontainer-sandbox:previous`.
Old builds are removed afterwards (`docker image prune`).

The image does not update the project's own dependencies (npm, pip, cargo) in
the project folder. Security of the host itself (kernel, Docker) comes from the
host's own updates.

## Host setup

Once per host, in the clone of this repository:

```sh
python3 host/setup.py
devcontainer-build-image --full        # first build, takes a while
```

[setup.py](host/setup.py) links `~/.local/share/devcontainer-sandbox` to this
clone's `host/` directory, creates the `devcontainer-start` and
`devcontainer-build-image` commands in `~/.local/bin`, installs the systemd
units, starts the daily build timer (`--no-timer` to skip that), puts a
configuration template in `~/.config/devcontainer-sandbox/config` and adds a
**Dev container** entry to the file manager: right-click a project folder,
*Open With*, and it starts the container and opens the editor. Started that
way it runs in a terminal window that stays open if something went wrong. Since
nothing is copied, changes in `host/` take effect right away and setup.py only
runs once; the fixed path is what the systemd unit needs.

That configuration file holds everything that must not be in a repository: the
server dev containers may reach, the mail account for failed builds, and the
git identity for commits made inside a container (see
[config.example](host/config.example)).

Committing in a container needs a name and an email address.
[initialize.py](host/initialize.py) writes one gitconfig per project to
`~/.config/devcontainer-sandbox/gitconfig-<project>` before every start, and
the project mounts it read-only as `~/.gitconfig`. Credential helpers are
never copied — there is nothing to push to from inside a container.

The identity is taken from the first of these that has one, so a project can
commit under a different name than the host:

1. `GIT_USER_NAME` and `GIT_USER_EMAIL` in the project's
   `.devcontainer/sandbox.env`
2. the same two settings in `~/.config/devcontainer-sandbox/config`
3. the host's own git identity

One file per project, because a shared one would change the identity under
another project's running container. With `--server`, setup.py also runs
[setup-ca.py](host/setup-ca.py), see [Server access](#server-access).

The scripts run on the host with your rights, and the image definition next to
them ends up as root in every container, so **never mount this clone
read-write into a container**. Copying the scripts somewhere else would not
help: `devcontainer-build-image` reads `image/` from the clone either way.

The features are pinned in a `devcontainer-lock.json` the CLI writes while
building; a full build deletes it first, which is how the features reach their
newest version. The file is not part of the repository.

Changes in `image/` are picked up by the next build
(`devcontainer-build-image` to build right away).

Checking the daily build:

```sh
systemctl --user list-timers devcontainer-build-image.timer
journalctl --user -u devcontainer-build-image.service
```

A failed build shows a notification that stays until it is dismissed, and can
send a mail with the last journal lines
([build-finished.py](host/build-finished.py)). Fill in the `SMTP_` and `MAIL_`
settings in `~/.config/devcontainer-sandbox/config`, including the password.
That file is `chmod 600`, and the scripts warn if it is not.

Use a **send-only** mailbox, never the main mail account: its password sits on
the laptop, the machine this sandbox exists to protect. Without those settings
only the notification appears. Testing both paths without waiting for a broken
build:

```sh
SERVICE_RESULT=success  ~/.local/share/devcontainer-sandbox/build-finished.py
SERVICE_RESULT=failed   ~/.local/share/devcontainer-sandbox/build-finished.py
```

Requirements: Docker (Linux), and an editor with a Remote SSH extension, e.g.
VSCodium with `jeanp413.open-remote-ssh`. Without a local
[devcontainer CLI](https://github.com/devcontainers/cli), the scripts build and
use a container image with it (Linux and Docker only).

Editor settings (`settings.json`):

```json
"remote.SSH.defaultExtensions": ["anthropic.claude-code"],
"remote.SSH.enableAgentForwarding": false,
"dev.containers.gitCredentialHelperConfigLocation": "none",
"dev.containers.copyGitConfig": false
```

VS Code Dev Containers (instead of SSH) forwards the host SSH agent and there is
no setting to disable it
([microsoft/vscode-remote-release#11413](https://github.com/microsoft/vscode-remote-release/issues/11413)).
If it is used anyway, VS Code must be started without `SSH_AUTH_SOCK`: a
wrapper `~/.local/bin/code` that runs `unset SSH_AUTH_SOCK` before starting
`/usr/share/code/bin/code`, and the `.desktop` launchers in
`~/.local/share/applications/` with `env -u SSH_AUTH_SOCK` in every `Exec=`
line. The sandbox removes a forwarded agent on attach and prints a red warning.

## New project

```sh
mkdir -p ~/projects/NAME
cp -r template/.devcontainer ~/projects/NAME/
# set "name"; add project-only tools to "features" if needed;
# optional: Wayland, GPU (devcontainer.json), server access (sandbox.env)
devcontainer-start ~/projects/NAME
```

`devcontainer-start` options: `--rebuild` after changing `devcontainer.json`, `--no-open`
to only start the container, `--editor CMD`, `--yes` to add the `~/.ssh/config`
entry without asking. The first start asks before adding a
`Host devcontainer-<name>-<hash>` block to `~/.ssh/config`.

Each project gets its own container, SSH entry and volumes; they can run side by
side. Containers have no access to remote repositories.

## Adding a tool

- Debian package: add it to [image/.devcontainer/Dockerfile](image/.devcontainer/Dockerfile).
- Anything else: add a feature to [image/.devcontainer/devcontainer.json](image/.devcontainer/devcontainer.json)
  (see [containers.dev/features](https://containers.dev/features)).

A feature can weaken the sandbox for every project: its `capAdd` and
`securityOpt` end up in the image metadata and are applied to every container.
The Rust feature, for example, sets `seccomp=unconfined`, which turns off
Docker's syscall filter; it is installed in the Dockerfile instead. After
adding a feature, check what it brought along:

```sh
docker image inspect local/devcontainer-sandbox:latest \
  -f '{{index .Config.Labels "devcontainer.metadata"}}' | python3 -m json.tool |
  grep -E 'capAdd|securityOpt|privileged|"id"'
```

Add it to the tool table in [image/claude-sandbox.md](image/claude-sandbox.md)
too, so Claude Code in the containers knows about it. That file is loaded as
managed instructions (`/etc/claude-code/CLAUDE.md`), since `~/.claude/CLAUDE.md`
is taken by the host's global instructions and `~/.claude` is a volume that
image updates do not reach.

Then `devcontainer-build-image`; projects get the tool on their next start.
Adding is safe, removing may break projects that use the tool. A tool only one
project needs goes into that project's `devcontainer.json` instead.

## Browsers

The image only has the system libraries (installing them needs root). Each
project downloads the browser matching its own Playwright version, as the normal
user. To keep the download across the daily rebuild, store it in the project
folder (add `.cache/` to `.gitignore`):

```jsonc
"containerEnv": {
  "PLAYWRIGHT_BROWSERS_PATH": "${containerWorkspaceFolder}/.cache/ms-playwright"
},
"postCreateCommand": "npx playwright install chromium"
```

For Claude Code to control a browser, use the Playwright MCP server with
Playwright's Chromium (its default, Google Chrome, needs root to install):

```sh
claude mcp add playwright -- npx @playwright/mcp@latest --browser chromium --headless
```

Browsers run **headless**; to watch them, enable [Wayland](#wayland) and drop
`--headless`. The browser is subject to the container firewall (no local
network). Playwright starts Chromium without Chromium's own sandbox, which
cannot work under `no-new-privileges`: a malicious page exploiting a browser
bug lands in the container, still inside the container sandbox.

## Wayland

Optional per project: GUI apps started in the container open as windows on the
host desktop (GNOME, Wayland session). Only the Wayland socket is passed
through: no X11, no D-Bus, no GPU (unless [GPU](#gpu) is enabled too).

Enable it in the project's `devcontainer.json` by uncommenting the Wayland mount
and the `containerEnv` block (see the template), then
`devcontainer-start --rebuild`. Start it from a terminal in the GNOME session,
so `WAYLAND_DISPLAY` and `XDG_RUNTIME_DIR` are set. After logging out and in
again the socket is new: start the container again (suspend and screen lock do
not matter).

GTK and Firefox use Wayland directly; Electron apps via
`ELECTRON_OZONE_PLATFORM_HINT`; Chromium may need `--ozone-platform=wayland`;
Qt apps need `qt6-wayland` in the image. Apps without Wayland support do not
start (no X11 on purpose).

What Wayland prevents: reading keystrokes or screen contents of other windows,
injecting input into them. What remains possible:

- **Fake windows.** A container app can show a window that looks like a
  KeePassXC unlock dialog; GNOME does not mark where a window comes from. Only
  type a master password into a dialog you opened yourself.
- **Clipboard.** While one of its windows has focus, the container can read the
  clipboard (e.g. a copied password) and replace it (e.g. with a command you
  then paste into a host terminal).
- **Compositor bugs.** A client exploiting a bug in GNOME Shell would run in the
  desktop session, outside the container.

## GPU

Optional per project, e.g. for OpenGL/Vulkan or GPU compute (Intel, AMD;
NVIDIA needs the NVIDIA container toolkit and is not covered). Uncomment the
GPU entry in `runArgs` (see the template), then `devcontainer-start --rebuild`.

Access to the device files needs a group the container does not know: the
host's `render` group exists only by number there. `--group-add` would not help
either, because it applies to the container's own process, while an SSH login
rebuilds its group list from `/etc/group` inside the container. So
`devcontainer-start` reads the group ids from the devices themselves and adds
the user to them on every start; nothing has to be configured.

Check it inside the container with `vulkaninfo --summary`: without the GPU it
reports `llvmpipe`, with it the real card. `wayland-info` and `eglinfo` are
there as well.

Without the GPU, OpenGL/Vulkan use software rendering (Mesa llvmpipe). With it, the
container talks directly to the GPU driver in the host kernel, which is a
larger attack surface: GPU drivers are a common way to escalate privileges.

## Server access

A container can get SSH access to one server, as one user, for 24 hours. On
every `devcontainer-start` the container creates a new key pair; the host signs
its public key with an SSH certificate authority (CA). The private key never
leaves the container, and nothing has to be added to or removed from the
server's `authorized_keys`: expired certificates are simply rejected.

Certificates carry no port, agent or X11 forwarding (`-O clear`), only a
terminal.

### Once per host and server

Every project logs in as **its own user** on the server, so that a compromised
container reaches only that project's files. Name the server and those users in
`~/.config/devcontainer-sandbox/config`:

```sh
SERVER_ADMIN=root@server.example.org
SERVER_PRINCIPAL_USERS=gameserver,blog
```

```sh
~/.local/share/devcontainer-sandbox/setup-ca.py --dry-run   # read the commands
~/.local/share/devcontainer-sandbox/setup-ca.py
```

[setup-ca.py](host/setup-ca.py) creates the CA if it does not exist yet
(`~/.config/devcontainer-sandbox/ssh-ca/`), copies its public half to the
server, adds `TrustedUserCAKeys` and `AuthorizedPrincipalsFile` in
`/etc/ssh/sshd_config.d/`, and reloads sshd after `sshd -t` accepted the
configuration. For every user it writes a principals file, and creates the
user first if the server does not have it: `useradd --create-home`, no
password, no group beyond its own.

`root` is accepted, with a warning. It is worth understanding what it costs:
everything in that container — the agent and every dependency it installs —
can then take the server over permanently, since root can leave a key of its
own behind and the 24-hour expiry does nothing against that. The laptop stays
protected, the server does not. Grant it only to a project whose job is
provisioning that server, and prefer a disposable server for testing.

A new project therefore means one more user: add it to the list and run
setup-ca.py again. Users that are already there stay as they are — an existing
account is never modified, only its principals file is written again with the
same content. Whatever rights that user needs for its job — a deploy
directory, one `sudo` rule — you grant on the server; this script only creates
a plain user and lets it log in.

**The script only ever adds.** Removing a user from `SERVER_PRINCIPAL_USERS`
does not take its access away: the principals file stays on the server and its
project keeps logging in. Revoking is a manual step, and it works immediately,
even for certificates that are still valid:

```sh
ssh root@server.example.org 'rm /etc/ssh/devcontainer_principals/blog'
```

The account and its files stay; only the certificate login is gone. That
asymmetry is deliberate: a script for dev containers should not delete things
on a server by itself.

The CA key can create certificates for every user a server accepts it for, so
protect it like your own SSH key. It has no passphrase because
`devcontainer-start` signs without asking.

Only certificate logins are affected; normal keys in `authorized_keys` keep
working. **The server decides** which users containers may become: a project
asking for a user without a file in `/etc/ssh/devcontainer_principals/` gets
nothing. Prefer a dedicated user without root. Removing a user's file cuts off
all containers at once, before their certificates expire.

### Per project

In `.devcontainer/sandbox.env` (read-only from inside the container):

```sh
SERVER_SSH_HOST=vserver.example.com
SERVER_SSH_USER=deploy
SERVER_SSH_PORT=22
SERVER_SSH_ALIAS=server
```

The host must have connected to the server once, since the server's host key
is copied from the host's `~/.ssh/known_hosts` into the container (strict
checking there). Inside the container: `ssh server`.

`devcontainer-start` prints how long the certificate is valid. After 24 hours
run `devcontainer-start` again; it replaces key and certificate even if the
container is still running. Starting the container any other way (e.g. VS Code
"Reopen in Container") creates no certificate.

## Rolling back

If a build breaks something, point the project at the previous build,
`"image": "local/devcontainer-sandbox:previous"`, and switch back to `latest`
once it is fixed. `previous` is overwritten by the next build.

## Verify

The attach output shows `sandbox: no host SSH agent forwarded`. In a container
terminal:

```sh
curl -sI https://github.com | head -1               # works
curl -m 5 http://192.168.1.1                        # fails (LAN, use your router)
sudo -n true                                        # fails
ls /tmp/vscode-ssh-auth-*.sock; echo $SSH_AUTH_SOCK # nothing
ssh-add -l                                          # no agent
git config --global --list                          # no host config
echo $DISPLAY                                       # empty (no X11)
```

## Rules and limitations

- Never add a feature, mount or `runArgs` entry that gives access to Docker
  (`docker-in-docker`, `docker-outside-of-docker`, `/var/run/docker.sock`) or
  to the X11 display (`/tmp/.X11-unix`, `DISPLAY`): the first is root on the
  host, the second lets the container read every keystroke on the desktop.
- Review every change to a project's `.devcontainer/` before rebuilding; it
  defines the sandbox and is only read-only from inside.
- Never mount the clone of this repository read-write into a container: its
  scripts run on the host, and its image definition runs as root at build time.
- The container shares the host kernel. A kernel exploit could escape it; a VM
  gives stronger isolation.
- Files in the project folder (e.g. git hooks, scripts) can be modified from
  inside. Review them before running anything on the host.
- The editor keeps a communication channel between its server in the container
  and the local UI.
- `~/.claude/CLAUDE.md` is bind-mounted as a file. If an editor saves it by
  replacing the file (new inode), the container keeps seeing the old version
  until it is restarted.

## License

GNU General Public License v3.0, see [LICENSE](LICENSE).
