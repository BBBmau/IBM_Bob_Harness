# Bob Shell harness container.
# Ubuntu is the friendliest base for a `curl | bash` installer: it ships the
# certs + glibc that most IBM tooling expects, and apt makes deps trivial.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    # Candidate install locations for the `bob` binary + a place for our API.
    PATH="/root/.local/bin:/root/.bob/bin:/usr/local/go/bin:/root/go/bin:/usr/local/bin:/usr/bin:${PATH}" \
    BOB_MODE=unrestricted-dev \
    # Bob runs from the container root so it governs the WHOLE container.
    BOB_WORKDIR=/

# System deps: curl + certs for the installer, bash for the install script,
# git for repo work inside the container, python for the REST wrapper,
# make + unzip for `make provider`, gh CLI for opening PRs from the Validator.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        bash \
        git \
        cron \
        python3 \
        python3-pip \
        python3-venv \
        make \
        unzip \
    && rm -rf /var/lib/apt/lists/*

# GitHub CLI — used by the Validator agent to open PRs via `gh pr create`.
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) \
      signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] \
      https://cli.github.com/packages stable main" \
      | tee /etc/apt/sources.list.d/github-cli.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends gh && \
    rm -rf /var/lib/apt/lists/* && \
    gh --version

# Bob Shell is a Node.js app and requires Node >= 22.15. Install Node 22 LTS
# from NodeSource before installing Bob.
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && node --version

# Go — required by both agents to run `make provider` and `go build ./...`.
# Bump GO_VERSION to upgrade. Releases: https://go.dev/dl/
ARG GO_VERSION=1.22.4
RUN curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz" \
      | tar -C /usr/local -xz && \
    go version

# Install a PINNED Bob Shell version for reproducible builds. The official
# `curl | bash` installer always grabs "latest"; instead we install the exact
# release tarball via npm (which is what the installer does under the hood).
# Bump BOB_VERSION to upgrade. Verify releases at:
#   https://s3.us-south.cloud-object-storage.appdomain.cloud/bob-shell/bobshell-version.txt
ARG BOB_VERSION=1.0.5
RUN npm install -g --loglevel=error \
        "https://s3.us-south.cloud-object-storage.appdomain.cloud/bob-shell/bobshell-${BOB_VERSION}.tgz" \
    && bob --version

# Bob config lives in ONE place: the container root /.bob (project-level config
# for the whole container, since Bob runs with cwd=/). It holds custom_modes.yaml
# (the "settings") + rules-unrestricted-dev/ (the AGENT.md rules).
# Note: Bob still auto-creates its own runtime state under /root/.bob at startup
# (settings.json license/auth, installation_id, trustedFolders.json, tmp/) — that
# dir is managed by Bob itself, not by us.
COPY .bob/ /.bob/

# REST API wrapper around `bob -p`.
WORKDIR /app
COPY api/requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /app/requirements.txt
COPY api/ /app/
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Bob edits are scoped to the starting dir (--yolo). We run from / so Bob can
# reach the whole container; /workspace remains as the compose volume mount.
RUN mkdir -p /workspace
WORKDIR /

EXPOSE 8080

# Report container readiness via the API's own liveness endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8080/health || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["serve"]
