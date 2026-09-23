# Dev container sandbox

A dev container to let a coding agent work in: it has internet access and write
access to one project folder, and nothing else from the machine it runs on.

A coding agent runs commands nobody read first, and installs dependencies nobody
audited. The usual dev container does not contain that: it mounts the host home
directory, forwards the SSH agent, copies git credentials and can reach every
device on the local network. This repository builds one that does not.

One image, `local/devcontainer-sandbox`, is built **on the host** and rebuilt
every day. Every project runs a container from that same image and adds only
what it alone needs, so the tools take disk space once instead of once per
project. There is no registry account, no CI, and nothing to push: the image
never leaves the machine.

## Contents

- [What the sandbox guarantees](#what-the-sandbox-guarantees)
- [What it does not protect against](#what-it-does-not-protect-against)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Tools in the image](#tools-in-the-image)
- [Dependencies: npm and Cargo defaults](#dependencies-npm-and-cargo-defaults)
- [How updates work](#how-updates-work)
- [Host setup in detail](#host-setup-in-detail)
- [Starting a project](#starting-a-project)
- [Adding a tool](#adding-a-tool)
- [Browsers](#browsers)
- [Wayland](#wayland) · [Audio](#audio) · [GPU](#gpu)
- [Server access](#server-access)
- [Fetching from GitHub](#fetching-from-github)
- [Rolling back](#rolling-back)
- [Verify the sandbox](#verify-the-sandbox)
- [Repository layout](#repository-layout)
- [Rules when changing it](#rules-when-changing-it)
- [Scope and contributions](#scope-and-contributions)
- [License](#license)

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
| Host git credentials | not forwarded; containers can fetch from GitHub, but not push ([Fetching from GitHub](#fetching-from-github)) |
| git identity (name, email) | passed through read-only, so commits work ([Host setup](#host-setup-in-detail)) |
| `.devcontainer/` of the project | read-only |
| Project folder | read-write |
| Host desktop (Wayland), audio, GPU | not available, unless enabled per project ([Wayland](#wayland), [Audio](#audio), [GPU](#gpu)) |
| Server via SSH | not available, unless enabled per project ([Server access](#server-access)) |

The firewall, the dropped privileges and the volumes are carried in the image's
metadata, so they apply to every project automatically — a project cannot forget
them, it can only be written to punch a hole in them deliberately.

## What it does not protect against

Worth reading before trusting it with anything:

- **The kernel is shared.** A kernel exploit escapes the container. A VM gives
  stronger isolation; this trades some of that for using the machine's tools,
  GPU and desktop.
- **The project folder is writable**, including git hooks, `Makefile`s and
  scripts that run later **on the host**. Review what comes out of a container
  before running it outside. A `git push` on the host runs the hooks and the
  `.git/config` of the project, so the same applies to pushing.
- **Anything enabled per project widens it.** A Wayland socket allows fake
  windows and clipboard access, an audio socket allows recording the
  microphone, a GPU device is a large kernel attack surface, and server access
  is exactly as dangerous as the user it logs in as. Each of those sections
  spells out what it costs.
- **The editor keeps a channel** between its server inside the container and
  the local UI.
- **Whatever the agent can reach on the internet**, it can also send data to.
  The firewall blocks the local network, not exfiltration.

## Requirements

- **Linux with Docker.** Root inside the container is dropped, so rootless
  Docker is not required, but the host's Docker daemon still runs as root — a
  hole poked into the container config (see [Rules](#rules-when-changing-it))
  can therefore be a hole into the host.
- **Python 3.10 or newer** on the host. The host scripts use nothing else.
- **systemd user session**, for the daily build timer.
- **An editor with a Remote SSH extension**, e.g. VSCodium with
  `jeanp413.open-remote-ssh`. The editor connects to the container over SSH,
  not through the Dev Containers extension (see
  [Host setup](#host-setup-in-detail) for why).
- Optional: a **Wayland desktop** (developed on GNOME) for the window, audio
  and file-manager integration.
- A local [devcontainer CLI](https://github.com/devcontainers/cli) is used if
  present; otherwise the scripts build a small container image with it.

## Quick start

```sh
git clone https://github.com/glashoff/devcontainer-sandbox.git
cd devcontainer-sandbox
python3 host/setup.py
devcontainer-build-image --full        # first build, takes a while
```

Then, for a project:

```sh
mkdir -p ~/Projects/NAME
cp -r template/.devcontainer ~/Projects/NAME/
# set "name" in devcontainer.json
devcontainer-start ~/Projects/NAME
```

`devcontainer-start` builds nothing by itself: it starts the container from the
current image, signs a certificate if the project uses [server
access](#server-access), and opens the editor over SSH. With no argument it
takes the current directory.

Keep the clone where it is. [setup.py](host/setup.py) links
`~/.local/share/devcontainer-sandbox` to its `host/` directory rather than
copying anything, so changes take effect immediately and setup only ever runs
once.

## Tools in the image

- Node LTS (nvm), Claude Code, GitHub CLI, git
- Python (Debian's)
- C/C++: gcc (`build-essential`), clang, clangd, clang-format, clang-tidy,
  LLVM, lld, gdb, cmake, ninja
- Rust: rustup with the current stable toolchain, rustfmt, clippy,
  cargo-audit, cargo-deny
- Chromium and Firefox for Playwright, ready to use ([Browsers](#browsers))
- Wayland client libraries, Mesa (OpenGL/Vulkan, software rendering without GPU)

Per project, in Docker volumes that survive rebuilds: the Claude Code login
(`claude-code-config-<id>`) and VSCodium's remote server with its extensions
(`vscodium-server-<id>`).

The list is one person's toolbox, and editing it is expected — see
[Adding a tool](#adding-a-tool).

## Dependencies: npm and Cargo defaults

The sandbox limits what a hijacked package can reach; these defaults make it
less likely that one gets installed at all. Popular packages are hijacked now
and then, and most such releases are found and removed within hours or days.

**npm** reads [image/.devcontainer/npmrc](image/.devcontainer/npmrc) as its
global configuration (`/etc/npmrc`):

- `min-release-age=7`: only versions published at least 7 days ago are
  installed. A security fix that cannot wait: `npm install PACKAGE@VERSION
  --min-release-age=0`, or list the package under `min-release-age-exclude`
  in the project's `.npmrc`.
- `ignore-scripts=true`: install scripts of dependencies do not run. A native
  module that needs its build: `npm rebuild PACKAGE --ignore-scripts=false`,
  after looking at what it runs.

Both are the lowest-priority settings, so a project's `.npmrc` overrides them.
pnpm, Yarn and Bun do not read them; they have their own settings
(`minimumReleaseAge`, `npmMinimalAgeGate`), set per project. Claude Code itself
is exempt, the daily build installs its newest release.

**Cargo** has no stable cooldown yet (`-Z min-publish-age` exists on nightly
only), and build scripts and procedural macros cannot be switched off. The
image adds `cargo-audit` and `cargo-deny` instead; build with `--locked` and
update crates one at a time. Here, the container is the main protection.

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

The timer runs the build daily; if the machine was off or suspended, it catches
up afterwards (it never wakes it). `devcontainer-start` then sees the new image
and recreates the container. Only the project folder and the volumes survive,
which is where everything of value lives; tools installed by hand inside the
container are gone. A container that keeps running for days is not updated;
start it again with `devcontainer-start` now and then. `devcontainer-start`
warns if the image is older than 48 hours.

Before each build the current image is tagged
`local/devcontainer-sandbox:previous`. Old builds are removed afterwards
(`docker image prune`).

The image does not update a project's own dependencies (npm, pip, cargo) in the
project folder. Security of the host itself (kernel, Docker) comes from the
host's own updates.

Checking the daily build:

```sh
systemctl --user list-timers devcontainer-build-image.timer
journalctl --user -u devcontainer-build-image.service
```

## Host setup in detail

[setup.py](host/setup.py) links `~/.local/share/devcontainer-sandbox` to this
clone's `host/` directory, creates the `devcontainer-start` and
`devcontainer-build-image` commands in `~/.local/bin`, installs the systemd
units, starts the daily build timer (`--no-timer` to skip that), puts a
configuration template in `~/.config/devcontainer-sandbox/config` and adds a
**Dev container** entry to the file manager: right-click a project folder,
*Open With*, and it starts the container and opens the editor. Started that way
it runs in a terminal window that stays open if something went wrong.

Changes in `image/` are picked up by the next build
(`devcontainer-build-image` to build right away).

The features are pinned in a `devcontainer-lock.json` the CLI writes while
building; a full build deletes it first, which is how features reach their
newest version. That file is not part of the repository.

### The configuration file

`~/.config/devcontainer-sandbox/config` holds everything that must not be in a
repository: the server dev containers may reach, the mail account for failed
builds, and the git identity for commits made inside a container. It is parsed
as `KEY=VALUE` and never executed; every section is optional, and without it
that feature simply stays off. See [config.example](host/config.example) for the
documented list.

### Git identity in the container

Committing needs a name and an email address. [initialize.py](host/initialize.py)
writes one gitconfig per project to
`~/.config/devcontainer-sandbox/gitconfig-<project>` before every start, and the
project mounts it read-only as `~/.gitconfig`. Credential helpers are never
copied: pushing stays a job for the host. The same file also sets up fetching
from GitHub ([Fetching from GitHub](#fetching-from-github)).

The identity is taken from the first of these that has one, so a project can
commit under a different name than the host:

1. `GIT_USER_NAME` and `GIT_USER_EMAIL` in the project's
   `.devcontainer/sandbox.env`
2. the same two settings in `~/.config/devcontainer-sandbox/config`
3. the host's own git identity

One file per project, because a shared one would change the identity under
another project's running container.

### Mail about failed builds

A failed build shows a notification that stays until it is dismissed, and can
send a mail with the last journal lines
([build-finished.py](host/build-finished.py)). Fill in the `SMTP_` and `MAIL_`
settings in the configuration file, including the password. That file is
`chmod 600`, and the scripts warn if it is not.

Use a **send-only** mailbox, never the main mail account: its password sits on
the machine this sandbox exists to protect. Without those settings only the
notification appears. Testing both paths without waiting for a broken build:

```sh
SERVICE_RESULT=success  ~/.local/share/devcontainer-sandbox/build-finished.py
SERVICE_RESULT=failed   ~/.local/share/devcontainer-sandbox/build-finished.py
```

### Editor settings

```json
"remote.SSH.defaultExtensions": ["anthropic.claude-code"],
"remote.SSH.enableAgentForwarding": false,
"dev.containers.gitCredentialHelperConfigLocation": "none",
"dev.containers.copyGitConfig": false
```

VS Code Dev Containers (instead of SSH) forwards the host SSH agent and there is
no setting to disable it
([microsoft/vscode-remote-release#11413](https://github.com/microsoft/vscode-remote-release/issues/11413)).
That is why the editor connects over SSH here. If Dev Containers is used anyway,
VS Code must be started without `SSH_AUTH_SOCK`: a wrapper `~/.local/bin/code`
that runs `unset SSH_AUTH_SOCK` before starting `/usr/share/code/bin/code`, and
the `.desktop` launchers in `~/.local/share/applications/` with
`env -u SSH_AUTH_SOCK` in every `Exec=` line. The sandbox removes a forwarded
agent on attach and prints a red warning either way.

## Starting a project

```sh
cp -r template/.devcontainer ~/Projects/NAME/
# set "name"; add project-only tools to "features" if needed;
# optional: Wayland, audio, GPU (devcontainer.json), server access (sandbox.env)
devcontainer-start ~/Projects/NAME
```

`devcontainer-start` options: `--rebuild` after changing `devcontainer.json`,
`--no-open` to only start the container, `--editor CMD`, `--yes` to add the
`~/.ssh/config` entry without asking. The first start asks before adding a
`Host devcontainer-<name>-<hash>` block to `~/.ssh/config`.

Each project gets its own container, SSH entry and volumes; they can run side by
side. Containers can fetch from GitHub, but not push
([Fetching from GitHub](#fetching-from-github)).

## Adding a tool

- Debian package: add it to [image/.devcontainer/Dockerfile](image/.devcontainer/Dockerfile).
- Anything else: add a feature to [image/.devcontainer/devcontainer.json](image/.devcontainer/devcontainer.json)
  (see [containers.dev/features](https://containers.dev/features)).

A feature can weaken the sandbox for every project: its `capAdd` and
`securityOpt` end up in the image metadata and are applied to every container.
The Rust feature, for example, sets `seccomp=unconfined`, which turns off
Docker's syscall filter; it is installed in the Dockerfile instead. After adding
a feature, check what it brought along:

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
Adding is safe, removing may break projects that use the tool.

### A tool only one project needs

It does not belong in the shared image. Packages that need root to install go
into a project image instead, a `Dockerfile` next to the project's
`devcontainer.json`:

```dockerfile
FROM local/devcontainer-sandbox:latest
RUN apt-get update \
 && apt-get install -y --no-install-recommends PACKAGE... \
 && rm -rf /var/lib/apt/lists/*
```

```jsonc
"build": { "dockerfile": "Dockerfile" },   // instead of "image"
```

apt runs as root while that image is built; the container started from it has no
root, and the sandbox settings (firewall, `NET_ADMIN`, `no-new-privileges`,
volumes) are inherited from the base image's `devcontainer.metadata`.
`devcontainer-start` reads the `FROM` line, so such a project is recreated by
the daily build as well — which also rebuilds these layers every time, so keep
large downloads out of them.

## Browsers

Chromium and Firefox come with the image, in
`/usr/local/share/ms-playwright` (`PLAYWRIGHT_BROWSERS_PATH`), together with the
system libraries they need. Nothing has to be downloaded in a project.

Playwright pins **one browser revision per release**: each version names the
revision it wants in its own `browsers.json`, looks for exactly that directory,
and refuses to start any other build. The image therefore installs the browsers
with the same `playwright@latest` that a container will later invoke, and the
weekly rebuild moves both forward together — Playwright releases less often than
that, so they normally match.

When they do not (a release landed since the last build, or a project pins an
older Playwright for its own test suite), nothing breaks: the directory belongs
to the user, so Playwright downloads the missing revision next to the others.
That copy lives in the container's writable layer, which means it is private to
that container and gone after the next recreation — the next image build brings
the matching one.

Do not set `PLAYWRIGHT_BROWSERS_PATH` per project. It would point Playwright at
an empty directory and force a download that the image already covers.

A project only needs Playwright in its `package.json` if the **project** uses it
— a test suite that imports the API and has to pin a version for CI. For an
agent driving a browser, Playwright is a tool like `curl`, and the MCP server
below is enough.

For Claude Code to control a browser, use the Playwright MCP server with
Playwright's Chromium (its default, Google Chrome, needs root to install):

```sh
claude mcp add playwright -- npx @playwright/mcp@latest --browser chromium --headless
```

Browsers run **headless**; to watch them, enable [Wayland](#wayland) and drop
`--headless`. The browser is subject to the container firewall (no local
network). Playwright starts Chromium without Chromium's own sandbox, which
cannot work under `no-new-privileges`: a malicious page exploiting a browser bug
lands in the container, still inside the container sandbox.

## Wayland

Optional per project: GUI apps started in the container open as windows on the
host desktop (GNOME, Wayland session). Only the Wayland socket is passed
through: no X11, no D-Bus, no GPU (unless [GPU](#gpu) is enabled too).

Enable it in the project's `devcontainer.json` by uncommenting the Wayland mount
and the `containerEnv` block (see the template), then
`devcontainer-start --rebuild`. Start it from a terminal in the desktop session,
so `WAYLAND_DISPLAY` and `XDG_RUNTIME_DIR` are set. After logging out and in
again the socket is new: start the container again (suspend and screen lock do
not matter).

GTK and Firefox use Wayland directly; Electron apps via
`ELECTRON_OZONE_PLATFORM_HINT`; Chromium may need `--ozone-platform=wayland`;
Qt apps need `qt6-wayland` in the image. Apps without Wayland support do not
start (no X11 on purpose).

What Wayland prevents: reading keystrokes or screen contents of other windows,
injecting input into them. What remains possible:

- **Fake windows.** A container app can show a window that looks like a password
  manager's unlock dialog; the desktop does not mark where a window comes from.
  Only type a master password into a dialog you opened yourself.
- **Clipboard.** While one of its windows has focus, the container can read the
  clipboard (e.g. a copied password) and replace it (e.g. with a command you
  then paste into a host terminal).
- **Compositor bugs.** A client exploiting a bug in the compositor would run in
  the desktop session, outside the container.

## Audio

Optional per project, and only useful together with [Wayland](#wayland): the
container plays into the host's sound server. Uncomment the audio mount and
`PULSE_SERVER` in `devcontainer.json` (see the template), then
`devcontainer-start --rebuild`.

Passed through is PipeWire's PulseAudio socket (`$XDG_RUNTIME_DIR/pulse/native`),
not a sound card: the container talks to a server that decides what it may do,
and it cannot reach ALSA devices or the kernel's sound drivers. Programs using
libpulse then work as they are; programs using ALSA (SDL, Bevy's `bevy_audio`)
need ALSA's PulseAudio plugin as their default device, which is a project image,
not the shared one:

```dockerfile
FROM local/devcontainer-sandbox:latest
RUN apt-get update \
 && apt-get install -y --no-install-recommends libasound2-plugins \
 && rm -rf /var/lib/apt/lists/* \
 && printf 'pcm.!default pulse\nctl.!default pulse\n' > /etc/asound.conf
```

Check it with `speaker-test -t sine -l 1` (needs `alsa-utils`); on the host,
`pw-dump | grep application.name` then lists the container's client. What this
allows: the container can record from the microphone and see what is being
played, because the PulseAudio protocol has no way to grant only playback.

## GPU

Optional per project, e.g. for OpenGL/Vulkan or GPU compute (Intel, AMD; NVIDIA
needs the NVIDIA container toolkit and is not covered). Uncomment the GPU entry
in `runArgs` (see the template), then `devcontainer-start --rebuild`.

Access to the device files needs a group the container does not know: the host's
`render` group exists only by number there. `--group-add` would not help either,
because it applies to the container's own process, while an SSH login rebuilds
its group list from `/etc/group` inside the container. So `devcontainer-start`
reads the group ids from the devices themselves and adds the user to them on
every start; nothing has to be configured.

Check it inside the container with `vulkaninfo --summary`: without the GPU it
reports `llvmpipe`, with it the real card. `wayland-info` and `eglinfo` are
there as well.

Without the GPU, OpenGL/Vulkan use software rendering (Mesa llvmpipe). With it,
the container talks directly to the GPU driver in the host kernel, which is a
larger attack surface: GPU drivers are a common way to escalate privileges.

## Server access

A container can get SSH access to one server, as one user, for 24 hours. On
every `devcontainer-start` the container creates a new key pair; the host signs
its public key with an SSH certificate authority (CA). The private key never
leaves the container, and nothing has to be added to or removed from the
server's `authorized_keys`: expired certificates are simply rejected.

Certificates carry no port, agent or X11 forwarding (`-O clear`), only a
terminal — or not even that, see [One command instead of a
shell](#one-command-instead-of-a-shell).

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
configuration. For every user it writes a principals file, and creates the user
first if the server does not have it: `useradd --create-home`, no password, no
group beyond its own.

`root` is accepted, with a warning. It is worth understanding what it costs:
everything in that container — the agent and every dependency it installs — can
then take the server over permanently, since root can leave a key of its own
behind and the 24-hour expiry does nothing against that. The workstation stays
protected, the server does not. Grant it only to a project whose job is
provisioning that server, and prefer a disposable server for testing.

A new project therefore means one more user: add it to the list and run
setup-ca.py again. Users that are already there stay as they are — an existing
account is never modified, only its principals file is written again with the
same content. Whatever rights that user needs for its job — a deploy directory,
one `sudo` rule — you grant on the server; this script only creates a plain user
and lets it log in.

**The script only ever adds.** Removing a user from `SERVER_PRINCIPAL_USERS`
does not take its access away: the principals file stays on the server and its
project keeps logging in. Revoking is a manual step, and it works immediately,
even for certificates that are still valid:

```sh
ssh root@server.example.org 'rm /etc/ssh/devcontainer_principals/blog'
```

The account and its files stay; only the certificate login is gone. That
asymmetry is deliberate: a script for dev containers should not delete things on
a server by itself.

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

The host must have connected to the server once, since the server's host key is
copied from the host's `~/.ssh/known_hosts` into the container (strict checking
there). Inside the container: `ssh server`.

`devcontainer-start` prints how long the certificate is valid. After 24 hours
run `devcontainer-start` again; it replaces key and certificate even if the
container is still running. Starting the container any other way (e.g. VS Code
"Reopen in Container") creates no certificate.

### One command instead of a shell

A project that has exactly one job on the server — uploading a built website,
say — does not need a shell there. `SERVER_SSH_FORCE_COMMAND` puts that one
command into the certificate, and the server runs it for every login on it, no
matter what the container asks for:

```sh
SERVER_SSH_FORCE_COMMAND=/usr/bin/rrsync -wo /srv/www/example.com
```

The certificate then carries `force-command` and no `permit-pty`: a forced
command needs no terminal. A `command="..."` entry in `authorized_keys` would
not work here, because a certificate authenticates against the CA and sshd
never looks at `authorized_keys` for it.

**It can only narrow, never widen.** Which user a project may become at all is
decided on the server, by `SERVER_PRINCIPAL_USERS` in the host config; without
this setting that user gets a shell anyway. A project that writes nonsense here
only locks itself out.

**`ssh <alias>` is no longer interactive** with it, which for `rsync` is
exactly the normal case: `rsync` runs its own remote command over the
connection and never wanted a shell.

Without a server, the result can be checked on the container's certificate
directly — `force-command` under "Critical Options", no `permit-pty` under
"Extensions":

```sh
ssh-keygen -L -f ~/.ssh/sandbox_ed25519-cert.pub
```

## Fetching from GitHub

Inside a container, `git fetch` and `git pull` work for GitHub; `git push`
does not. Pushing stays a job for the host.

**Which repository belongs to the project** is written in
`.devcontainer/sandbox.env`, which the container cannot change:

```sh
GIT_REMOTE_URL=git@github.com:owner/repo.git
```

The deploy key below goes by this, never by `origin` in `.git/config`. The
container can rewrite `.git/config`, and if the host trusted it, a container
could point `origin` at another, private repository of yours and get a deploy
key for that one on the next start. If the two differ, `devcontainer-start`
warns and names both.

**The project's own repository.** When `GIT_REMOTE_URL` is a GitHub
repository, `devcontainer-start` gives the project a deploy key of its own: an
SSH key pair created on the host in
`~/.config/devcontainer-sandbox/deploy-keys/github-<project>-<owner>--<repo>`
and registered **read-only** with that one repository through the host's `gh`.
That needs `gh auth login` with the `repo` scope and admin rights on the
repository. On every start it checks that the key is still registered, adds it
again if it was deleted on GitHub, and copies the private key and GitHub's host
keys (from `gh api meta`) into the container's `~/.ssh`. The container's
gitconfig sends that repository over SSH with the key, so this works for private
repositories as well. A push is refused by GitHub: `The key you are
authenticating with has been marked as read only`.

If the key cannot be added (no `gh`, no admin rights, someone else's
repository), `devcontainer-start` warns and goes on, and the container fetches
over HTTPS as below. To stop it trying, set this in `.devcontainer/sandbox.env`:

```sh
GIT_DEPLOY_KEY=no
```

The key does not expire. Anything that runs in the container can copy it and
read the repository for as long as the key is registered, which matters for a
private repository. To revoke it, delete it on GitHub (Settings → Deploy keys,
or `gh repo deploy-key list` and `gh repo deploy-key delete`) together with
`~/.config/devcontainer-sandbox/deploy-keys/github-<project>-*`; the next start
creates a new one unless `GIT_DEPLOY_KEY=no`. Starting the container any other
way (e.g. VS Code "Reopen in Container") copies no key.

**Other public repositories.** HTTPS needs no credentials for them, but SSH
needs a key even there, so the container's gitconfig rewrites `git@github.com:`
and `ssh://git@github.com/` to `https://github.com/`, except for the project's
own repository. The project's `.git/config` is shared with the host and stays as
it is; only the container sees the rewrite.

## Rolling back

If a build breaks something, point the project at the previous build,
`"image": "local/devcontainer-sandbox:previous"`, and switch back to `latest`
once it is fixed. `previous` is overwritten by the next build.

## Verify the sandbox

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

## Repository layout

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
| [host/deploy_key.py](host/deploy_key.py) | the read-only GitHub deploy key of a project |
| [host/remote.py](host/remote.py) | the repository a project belongs to (`GIT_REMOTE_URL`), and its comparison with `.git/config` |
| [host/initialize.py](host/initialize.py) | creates the bind-mount sources on the host (`CLAUDE.md`, SSH key) |
| [host/devcontainer_cli.py](host/devcontainer_cli.py) | runs the devcontainer CLI (shared by the scripts) |
| [template/.devcontainer/](template/.devcontainer/) | starting point for a project: `devcontainer.json`, `sandbox.env` |

The sandbox settings (entrypoint, `NET_ADMIN`, `no-new-privileges`, volumes,
agent check) live in the image's metadata and apply to every project
automatically. What an image cannot carry stays in each project's
`devcontainer.json`: `runArgs`, `initializeCommand` and the bind mounts from the
host.

## Rules when changing it

- Never add a feature, mount or `runArgs` entry that gives access to Docker
  (`docker-in-docker`, `docker-outside-of-docker`, `/var/run/docker.sock`) or to
  the X11 display (`/tmp/.X11-unix`, `DISPLAY`): the first is root on the host,
  the second lets the container read every keystroke on the desktop.
- Review every change to a project's `.devcontainer/` before rebuilding; it
  defines the sandbox and is only read-only from inside.
- Never mount the clone of this repository read-write into a container: its
  scripts run on the host with your rights, and its image definition runs as
  root at build time. Copying the scripts elsewhere would not help —
  `devcontainer-build-image` reads `image/` from the clone either way.
- `~/.claude/CLAUDE.md` is bind-mounted as a file. If an editor saves it by
  replacing the file (new inode), the container keeps seeing the old version
  until it is restarted.

## Scope and contributions

This is a personal setup, published because the pieces were worth writing down,
not as a product. It assumes Linux, Docker, a Debian-based image, systemd and —
for the optional desktop parts — a Wayland session, and it was built and tested
on one laptop. There is no test suite, no compatibility promise and no support.

Fork it, take the parts you like. Issues and pull requests are welcome but may
sit for a while; anything that widens what a container can reach is unlikely to
be merged.

## License

GNU General Public License v3.0, see [LICENSE](LICENSE).
