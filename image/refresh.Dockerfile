# Daily update layer on top of the cached base image (see host/build-image.sh).
# REFRESH changes every day, so this layer is rebuilt daily while the large
# base layers stay the same and are not rebuilt or stored again.
# build-image.py always passes BASE; the default only keeps buildx from warning
# about an empty base image name.
ARG BASE=local/devcontainer-sandbox-base:latest
FROM ${BASE}

# What Claude Code in the container should know about the sandbox and its tools.
# /etc/claude-code/CLAUDE.md is always loaded and read-only for the container;
# ~/.claude/CLAUDE.md is taken by the host's global instructions.
COPY claude-sandbox.md /etc/claude-code/CLAUDE.md

ARG REFRESH
# Node comes from nvm in the base image, so this layer can pull a new LTS
# release; on most days nvm finds nothing to do and the layer stays small.
# Claude Code is installed afterwards, so it lands in the current version.
# It is exempt from the npm defaults in /etc/npmrc: it should be the newest
# release every day, and its postinstall script sets up its native binary.
RUN echo "Updates of ${REFRESH}" \
 && apt-get update \
 && apt-get -y upgrade \
 && rm -rf /var/lib/apt/lists/* \
 && bash -c '. "$NVM_DIR/nvm.sh" \
      && nvm install --lts \
      && nvm alias default "lts/*" \
      && ln -sfn "$NVM_DIR/versions/node/$(nvm version default)" "$NVM_DIR/current"' \
 && npm install -g --min-release-age=0 --ignore-scripts=false \
      @anthropic-ai/claude-code@latest \
 && npm cache clean --force
