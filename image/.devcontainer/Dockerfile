# KiCad, unpacked from the official AppImages. A stage of its own, so that
# the archives (981 MB) stay out of the image: only the unpacked trees are
# copied over. They are not downloaded during the build but kept next to this
# file - a pinned nightly name disappears from the server sooner or later,
# and a build of the image every project depends on must not hang on that
# (kicad/download.sh fetches them once; README "KiCad").
#
# --appimage-extract unpacks into a directory named AppDir and leaves
# squashfs-root beside it as a symlink to it, so AppDir is what is moved
# here; moving squashfs-root would move the link and leave the tree behind.
FROM mcr.microsoft.com/devcontainers/base:debian AS kicad
COPY kicad/*.AppImage.tar kicad/sha256sums /tmp/kicad/
RUN cd /tmp/kicad \
 && sha256sum -c sha256sums \
 && for archive in *.AppImage.tar; do \
      case "$archive" in *nightly*) target=nightly ;; \
                         *) target="$(echo "$archive" | sed -E 's/^kicad-([0-9.]+)-.*/\1/')" ;; \
      esac \
      && tar xf "$archive" \
      && image="$(echo "$archive" | sed 's/\.tar$//')" \
      && chmod +x "$image" \
      && "./$image" --appimage-extract >/dev/null \
      && mkdir -p "/opt/kicad/$target" \
      && mv AppDir "/opt/kicad/$target/AppDir" \
      && rm -f squashfs-root "$image" "$archive"; \
    done \
 && rm -rf /tmp/kicad
# The nightly uses the stable's libraries (KICAD10_*_DIR below), so its own
# are 2.5 GB for nothing. The demos are example projects that no project needs
# in order to work, 341 MB in each version; whoever wants them unpacks an
# archive in kicad/ by hand.
RUN rm -rf /opt/kicad/nightly/AppDir/share/kicad/symbols \
           /opt/kicad/nightly/AppDir/share/kicad/footprints \
           /opt/kicad/nightly/AppDir/share/kicad/3dmodels \
           /opt/kicad/nightly/AppDir/share/kicad/demos \
           /opt/kicad/10.0.6/AppDir/share/kicad/demos

FROM mcr.microsoft.com/devcontainers/base:debian

# Everything from Debian, kept current by the daily apt upgrade in
# refresh.Dockerfile. Add further Debian packages here.
RUN apt-get update \
 && apt-get -y upgrade \
 && apt-get install -y --no-install-recommends \
      # C/C++ toolchains
      build-essential gdb cmake ninja-build pkg-config \
      clang clangd clang-format clang-tidy lld llvm \
      # Wayland clients (optional Wayland passthrough) and OpenGL/Vulkan,
      # with software rendering when no GPU is passed through
      libwayland-client0 libwayland-cursor0 libwayland-egl1 libxkbcommon0 \
      libgl1 libegl1 libgl1-mesa-dri mesa-vulkan-drivers \
      fonts-dejavu-core \
      # Checking the optional passthroughs: wayland-info, eglinfo, vulkaninfo
      wayland-utils mesa-utils-bin vulkan-tools \
      # Virtual environments, since pip into the system Python is blocked
      python3-venv python3-pip \
      # Reading PDFs: pdftotext for the text, mutool for structure and for
      # rendering a page, qpdf for an encrypted or damaged file. pypdf is the
      # same from Python, with cryptography for AES-encrypted files and
      # fonttools, without which it warns its way through every font it meets
      poppler-utils mupdf-tools qpdf \
      python3-pypdf python3-cryptography python3-fonttools \
      curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Node via nvm, installed here instead of via ghcr.io/devcontainers/features/node:1.
# The paths are the feature's ($NVM_DIR/current/bin), so everything that relies
# on them keeps working; owned by the user, so global npm packages can be
# installed without root. The daily update layer refreshes the LTS version
# (see refresh.Dockerfile), which a feature could not do.
ENV NVM_DIR=/usr/local/share/nvm
ENV PATH=$NVM_DIR/current/bin:$PATH
RUN git clone https://github.com/nvm-sh/nvm.git "$NVM_DIR" \
 && git -C "$NVM_DIR" checkout "$(git -C "$NVM_DIR" describe --abbrev=0 --tags)" \
 && rm -rf "$NVM_DIR/.git" \
 && bash -c '. "$NVM_DIR/nvm.sh" \
      && nvm install --lts \
      && nvm alias default "lts/*" \
      && ln -sfn "$NVM_DIR/versions/node/$(nvm version default)" "$NVM_DIR/current"' \
 && chown -R vscode:vscode "$NVM_DIR"

# npm defaults against hijacked packages: a 7-day cooldown for new releases and
# no install scripts from dependencies (see the file). Outside the nvm
# directory, so that a new Node version in the daily update layer keeps them.
# They apply from here on, so Playwright below is installed with them as well.
COPY npmrc /etc/npmrc
ENV NPM_CONFIG_GLOBALCONFIG=/etc/npmrc

# Playwright: the library and the browsers it drives, so that nothing has to be
# downloaded at run time. It is needed more often than for tests - a shop or a
# parts catalogue that builds its page in JavaScript has no content for curl,
# and a headless browser is the way to read it (see claude-sandbox.md "Pages
# that need JavaScript").
#
# playwright-core is the library without the test runner. It lives in a folder
# of its own instead of as a global npm package, because the daily update layer
# may move Node to a new LTS release and a global package stays behind in the
# version that installed it. NODE_PATH makes require() find it from any folder,
# while a project's own node_modules still comes first.
#
# Playwright pins one browser revision per release (its browsers.json) and
# refuses to start anything else, so the browsers are installed by exactly the
# version installed here, not by whatever "latest" means a layer later. The
# weekly rebuild moves both forward together.
#
# PLAYWRIGHT_BROWSERS_PATH is the registry directory, for reading *and*
# writing. It belongs to the user, not to root: a Playwright version that wants
# a revision this image does not have then downloads it next to the others
# instead of failing with a permission error. That copy lives in the
# container's writable layer, so it is neither shared with another project nor
# kept beyond the next recreation.
ENV PLAYWRIGHT_BROWSERS_PATH=/usr/local/share/ms-playwright \
    NODE_PATH=/opt/node-tools/node_modules
RUN npm install --prefix /opt/node-tools playwright-core \
 && version="$(node -p "JSON.parse(require('fs') \
      .readFileSync('$NODE_PATH/playwright-core/package.json','utf8')).version")" \
 && echo "playwright-core $version" \
 && npx --yes "playwright@$version" install chromium firefox \
 && chown -R vscode:vscode "$PLAYWRIGHT_BROWSERS_PATH" \
 && rm -rf /root/.npm

# Rust, installed here instead of via ghcr.io/devcontainers/features/rust:1:
# that feature adds "capAdd": ["SYS_PTRACE"] and "securityOpt":
# ["seccomp=unconfined"] to the image metadata, which would turn off Docker's
# seccomp filter for every project using this image (see README "Adding a
# tool"). The paths match the feature's, so nothing else changes.
ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:$PATH
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
      | sh -s -- -y --no-modify-path --profile minimal \
          --default-toolchain stable -c rustfmt -c clippy \
 && chown -R vscode:vscode "$RUSTUP_HOME" "$CARGO_HOME"

# Checks of a project's crates: cargo-audit against the RustSec advisory
# database, cargo-deny for advisories, licenses and allowed sources. Built
# from source with their own lockfiles; the registry cache is dropped again.
RUN cargo install --locked cargo-audit cargo-deny \
 && rm -rf "$CARGO_HOME/registry" "$CARGO_HOME/git" \
 && chown -R vscode:vscode "$CARGO_HOME"

# Go, installed here instead of via ghcr.io/devcontainers/features/go:1, for
# the same reason as Rust: that feature adds "capAdd": ["SYS_PTRACE"] and
# "securityOpt": ["seccomp=unconfined"], which would turn off Docker's seccomp
# filter for every project using this image. The paths are the feature's
# (GOROOT, GOPATH), so everything that expects them keeps working.
#
# The current release is picked at build time, so the weekly rebuild moves Go
# forward by itself. Its checksum is published beside the archive and checked
# here; GOPATH belongs to the user, so "go install" needs no root.
ENV GOROOT=/usr/local/go \
    GOPATH=/go \
    PATH=/usr/local/go/bin:/go/bin:$PATH
RUN version="$(curl -fsSL 'https://go.dev/VERSION?m=text' | head -1)" \
 && archive="${version}.linux-$(dpkg --print-architecture).tar.gz" \
 && curl -fsSLO "https://dl.google.com/go/${archive}" \
 && curl -fsSL "https://dl.google.com/go/${archive}.sha256" \
      | sed "s|\$|  ${archive}|" | sha256sum -c - \
 && tar -C /usr/local -xzf "${archive}" \
 && rm "${archive}" \
 && mkdir -p "$GOPATH/bin" \
 && chown -R vscode:vscode "$GOPATH"

# Checks of a project's modules: govulncheck reports the known vulnerabilities
# that a build actually reaches, from the Go vulnerability database. Go needs
# no cooldown setting of its own (README "Dependencies"): the checksum
# database is on by default, and modules run no install scripts.
RUN go install golang.org/x/vuln/cmd/govulncheck@latest \
 && rm -rf "$GOPATH/pkg" /root/.cache/go-build \
 && chown -R vscode:vscode "$GOPATH"

# KiCad from the stage above: two versions, because 10.0.6 is the one users
# can go back to and the nightly (10.99, becomes 11) is the one with the
# schematic IPC API. Both are built against wxWidgets 3.3.2, which picks
# Wayland or X11 at run time, so they need nothing of ours to show a window
# (README "KiCad"). The libraries belong to the stable version and are used
# by both, through the KICAD10_* variables the tables below refer to.
COPY --from=kicad /opt/kicad /opt/kicad
ENV KICAD_STABLE_APPDIR=/opt/kicad/10.0.6/AppDir \
    KICAD_NIGHTLY_APPDIR=/opt/kicad/nightly/AppDir \
    KICAD10_SYMBOL_DIR=/opt/kicad/10.0.6/AppDir/share/kicad/symbols \
    KICAD10_FOOTPRINT_DIR=/opt/kicad/10.0.6/AppDir/share/kicad/footprints \
    KICAD10_3DMODEL_DIR=/opt/kicad/10.0.6/AppDir/share/kicad/3dmodels

# Starting them: kicad_stable and kicad_nightly, defined for interactive
# shells too, since /etc/bash.bashrc does not read /etc/profile.d. They go
# through AppRun, which links the bundled WebKit helpers into place - see the
# file, and README "KiCad".
COPY kicad-config/kicad.sh /etc/profile.d/kicad.sh
RUN printf '\n# Not read by /etc/profile.d for an interactive non-login shell.\n. /etc/profile.d/kicad.sh\n' \
      >> /etc/bash.bashrc

# KiCad asks at its first start about update checks, data collection and the
# library tables. Answered by files, in the home the image creates, so a
# fresh container asks nothing: enable_server for the IPC API, canvas_type 1
# (accelerated), the prompts switched off, the symbol and footprint tables
# from the stable version's own template, and an empty design block table
# (there is no template for that one, and the question it would ask offers
# libraries that do not exist here).
COPY kicad-config/kicad_common.json kicad-config/kicad.json \
     kicad-config/design-block-lib-table /tmp/kicad-config/
RUN for version in 10.0 10.99; do \
      directory="/home/vscode/.config/kicad/$version" \
      && mkdir -p "$directory" \
      && cp /tmp/kicad-config/kicad_common.json /tmp/kicad-config/kicad.json \
            /tmp/kicad-config/design-block-lib-table "$directory/" \
      && cp "$KICAD_STABLE_APPDIR/share/kicad/template/sym-lib-table" \
            "$KICAD_STABLE_APPDIR/share/kicad/template/fp-lib-table" "$directory/"; \
    done \
 && rm -rf /tmp/kicad-config \
 && chown -R vscode:vscode /home/vscode/.config/kicad

# Settings the programs themselves cannot place, all of them wanted for every
# window in every container, not only KiCad's:
#
# drirc turns vsync off for KiCad's windows. GNOME sends no frame callbacks
# to a covered window, Mesa's eglSwapBuffers then blocks the thread that also
# serves KiCad's API, and the API stops answering while a window is covered.
#
# The title-bar buttons take two files, because GTK has two sources for them
# and an application uses the one its GTK was built for. GTK draws the title
# bar itself and asks the desktop which buttons belong in it; in a container
# nobody answers, and the fallback is the close button alone - no minimise,
# no maximise. Debian's GTK reads the layout from gtk-3.0/settings.ini, an
# application with a GTK of its own (the KiCad AppImages) reads GSettings,
# whose only backend here is the keyfile one - hence GSETTINGS_BACKEND, which
# also gives such applications a place to keep their settings at all.
COPY kicad-config/drirc /etc/drirc
COPY kicad-config/gtk-settings.ini /etc/xdg/gtk-3.0/settings.ini
ENV GSETTINGS_BACKEND=keyfile
COPY kicad-config/glib-keyfile /tmp/glib-keyfile
RUN install -D -m 644 /tmp/glib-keyfile \
      /home/vscode/.config/glib-2.0/settings/keyfile \
 && rm /tmp/glib-keyfile \
 && chown -R vscode:vscode /home/vscode/.config/glib-2.0

# KiGenAI, the command with which a Claude edits the open KiCad editors
# through KiCad's IPC API. Pinned to a tag and checked against the commit that
# tag stood on, so a moved tag cannot change what is installed. The setup
# generates KiCad's Python client from pinned sources and brings its own
# protobuf compiler through grpcio-tools, so no system package is needed.
#
# The environment points back at /opt/kigenai through a .pth file, so the
# folder must stay where it is. Nothing is written there at run time: root
# owns it, everyone may read and run it. A new nightly or a fix means a new
# tag from the project; raise both lines here and rebuild.
ARG KIGENAI_TAG=v0.2.0
ARG KIGENAI_COMMIT=733d662fe3199a16536af65bef4147ba7ee942ce
RUN git clone --depth 1 --branch "$KIGENAI_TAG" \
      https://github.com/glashoff/kigenai /opt/kigenai \
 && test "$(git -C /opt/kigenai rev-parse HEAD)" = "$KIGENAI_COMMIT" \
 && cd /opt/kigenai \
 && python3 -m kigenai.setup \
 && rm -rf /opt/kigenai/.git \
 && ln -s /opt/kigenai/.venv/bin/kigenai /usr/local/bin/kigenai \
 && kigenai --help >/dev/null

# How to use it, as a skill rather than as part of /etc/claude-code/CLAUDE.md:
# of a skill only the description stands in every session's context, and the
# rules themselves are loaded when a Claude decides they apply - KiCad is not
# every project's business. /etc/claude-code/.claude/skills is where Claude
# Code looks for the skills of a whole machine. Assembled from the file that
# comes with the package, so that the rules exist as one text only.
COPY kigenai-skill-header.md /tmp/kigenai-skill-header.md
RUN mkdir -p /etc/claude-code/.claude/skills/kigenai \
 && cat /tmp/kigenai-skill-header.md /opt/kigenai/CLAUDE.md \
      > /etc/claude-code/.claude/skills/kigenai/SKILL.md \
 && rm /tmp/kigenai-skill-header.md
