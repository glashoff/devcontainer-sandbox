# Sandbox dev container

You run inside a sandboxed dev container (devcontainer-sandbox). Installed to
`/etc/claude-code/CLAUDE.md` by the image build.

## Limits

- No root, no `sudo`: you cannot install system packages (`apt`). If a tool is
  missing permanently, tell the user; it has to be added to the image
  (devcontainer-sandbox, `image/.devcontainer/`) on the host.
- Internet only: local networks (LAN, the Docker host) are blocked.
- No credentials for remote git repositories: `git fetch` and `git pull` work
  for public GitHub repositories (SSH remotes are rewritten to HTTPS), but do
  not try to push or to fetch private repositories; the user does that on the
  host.
- The container is recreated after every image update (usually daily). Only
  the project folder (`/workspaces/<project>`) and `~/.claude` survive;
  anything installed or written elsewhere is lost.

## Installed tools

| Tool | Where |
|---|---|
| Node LTS, npm, npx | nvm, `/usr/local/share/nvm/current/bin` |
| Claude Code | global npm package, updated by the image build |
| Python 3 (Debian), pip, venv | `/usr/bin/python3` |
| Rust (stable), cargo, rustfmt, clippy | `/usr/local/cargo/bin`, toolchains in `/usr/local/rustup` |
| gcc, g++, make (`build-essential`), gdb, cmake, ninja, pkg-config | `/usr/bin` |
| clang, clangd, clang-format, clang-tidy, LLVM, lld | `/usr/bin` (Debian packages) |
| GitHub CLI `gh`, git | `/usr/bin` (not logged in; git has a name and email, but no credentials; public GitHub repositories can be fetched) |
| Chromium and Firefox for Playwright | `/usr/local/share/ms-playwright` (`PLAYWRIGHT_BROWSERS_PATH`) |
| Wayland client libraries, Mesa (OpenGL/Vulkan) | system |

## Installing project dependencies without root

- Node: project-local `npm install` (into `node_modules/`).
- Python: pip into the system Python is blocked (Debian); use a virtual
  environment in the project: `python3 -m venv .venv`.
- Rust: crates via `Cargo.toml`. `cargo install` works but lands in
  `/usr/local/cargo/bin` and is lost on the next recreation.

## Browsers

Chromium and Firefox are already installed, so do not download anything:

```sh
npx @playwright/mcp@latest --browser chromium
```

Use Playwright's Chromium; its default, Google Chrome, would need root. Run
browsers headless unless `WAYLAND_DISPLAY` is set.

If Playwright still asks for `playwright install`, its version wants a browser
revision this image does not have. Running `npx playwright install chromium`
then works (the directory is writable), but it downloads a few hundred MB that
are lost on the next recreation — worth telling the user, since a rebuilt image
would have the right one.

## Optional, per project

- **GUI apps**: only if `WAYLAND_DISPLAY` is set (Wayland, no X11). GTK and
  Firefox work directly; Chromium may need `--ozone-platform=wayland`.
- **GPU**: only if `/dev/dri` exists; otherwise OpenGL/Vulkan use software
  rendering.
- **Server access**: only if `~/.ssh/config` has a host entry (usually
  `ssh server`). The certificate is valid for 24 hours; if login fails with an
  expired certificate, ask the user to run `devcontainer-start` on the host.
