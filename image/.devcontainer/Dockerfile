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
