# Sandbox dev container

You run inside a sandboxed dev container (devcontainer-sandbox). Installed to
`/etc/claude-code/CLAUDE.md` by the image build.

## Limits

- No root, no `sudo`: you cannot install system packages (`apt`). If a tool is
  missing permanently, tell the user; it has to be added to the image
  (devcontainer-sandbox, `image/.devcontainer/`) on the host.
- Internet only: local networks (LAN, the Docker host) are blocked.
- Git remotes are read-only: `git fetch` and `git pull` work for the
  project's own GitHub repository (read-only deploy key, private ones too) and
  for public GitHub repositories. Do not try to push; the user does that on
  the host. There are no credentials for other git hosts.
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
| cargo-audit, cargo-deny | `/usr/local/cargo/bin` |
| Go (current release), gofmt, go vet | `/usr/local/go/bin`, `GOPATH=/go` |
| govulncheck | `/go/bin` |
| gcc, g++, make (`build-essential`), gdb, cmake, ninja, pkg-config | `/usr/bin` |
| clang, clangd, clang-format, clang-tidy, LLVM, lld | `/usr/bin` (Debian packages) |
| GitHub CLI `gh`, git | `/usr/bin` (`gh` not logged in; git has a name and email, and can fetch from GitHub but not push) |
| Chromium and Firefox for Playwright | `/usr/local/share/ms-playwright` (`PLAYWRIGHT_BROWSERS_PATH`) |
| Wayland client libraries, Mesa (OpenGL/Vulkan) | system |

## Installing project dependencies without root

- Node: project-local `npm install` (into `node_modules/`).
- Python: pip into the system Python is blocked (Debian); use a virtual
  environment in the project: `python3 -m venv .venv`.
- Rust: crates via `Cargo.toml`. `cargo install` works but lands in
  `/usr/local/cargo/bin` and is lost on the next recreation.
- Go: modules via `go.mod`. `go install` lands in `/go/bin` and is lost on the
  next recreation as well.

## Supply chain defaults

Hijacked releases of popular packages are a real risk, so the image sets
defaults against them. Do not switch them off on your own; ask the user.

- npm (`/etc/npmrc`): `min-release-age=7` installs only versions published at
  least 7 days ago, and `ignore-scripts=true` skips the install scripts of
  dependencies. If a package fails because its install script did not run
  (native modules built with node-gyp), name it and ask the user before
  running `npm rebuild PACKAGE --ignore-scripts=false`. If an urgent security
  fix is newer than 7 days, `npm audit fix` warns about it; tell the user.
- Rust: there is no stable cooldown in Cargo, and `build.rs` scripts and
  procedural macros always run during a build. Build with `--locked`, update
  single crates on purpose (`cargo update -p CRATE`) instead of everything,
  and run `cargo audit` (and `cargo deny check` if the project has a
  `deny.toml`) after changing dependencies.
- Go: modules run no install scripts and the checksum database is on, so
  there is nothing to switch off. Run `govulncheck ./...` after changing
  dependencies and report what it finds.
- Double-check the exact name of every new package, crate or module before
  adding it; look-alike names (typosquatting) are a common attack.

## Protected files

Some of a project's files are mounted read-only: writing to them fails with
"Read-only file system", and that is deliberate, not a broken permission. Do
not work around it. Which ones they are is in `.devcontainer/devcontainer.json`,
as the mounts marked `readonly`.

- Propose the change instead: copy the file to the same path under
  `protected_draft/` (`protected_draft/Makefile`) and edit it there. That
  directory is empty until you put something in it; it holds proposals, not
  copies of everything.
- To remove a file or a directory, create a marker beside where it sits in
  the draft: `protected_draft/protected/old.md.delete` removes
  `protected/old.md`, and `protected_draft/protected/legacy.delete` removes
  that whole directory. Write the reason into the marker; it is shown to the
  user. **Deleting a file from the draft removes nothing** — it only
  withdraws your proposal, and the protected file stays as it is.
- Nothing takes effect until the user runs `devcontainer-approve` on the
  host, reads the diff and accepts it. Say so when you are done, instead of
  assuming the change is in place. If the user changed the same file in the
  meantime, that command stops and asks them to decide; copy the protected
  file over your draft and make your change again if they say so.
- Never commit `protected_draft/`. It carries a `.gitignore` of its own, and
  a push that contains it is refused.

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
