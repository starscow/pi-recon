# PI Recon container image.
# docker build --platform linux/amd64 -t pi-recon:tsec -f docker/Dockerfile .
#
# Never copy host tools/bin/*; host binaries may not match the target architecture.

ARG BASE_IMAGE=python:3.12-slim-bookworm
FROM ${BASE_IMAGE}

ARG TARGETARCH
ARG DEEPSEEK_BASE_URL="https://api.deepseek.com/v1"
ARG MODELS_FILE=models.tsec.json
ARG LLM_ROUTE=auto
ARG TSEC_HOSTED=""

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NO_PROXY='*' \
    no_proxy='*' \
    PATH="/app/tools/bin:/usr/local/bin:${PATH}" \
    PI_RECON_IN_DOCKER=1 \
    PI_RECON_LLM_BASE=${DEEPSEEK_BASE_URL} \
    PI_RECON_LLM_PROVIDER=deepseek \
    PI_RECON_LLM_MODEL=deepseek-v4-flash \
    PI_RECON_LLM_ROUTE=${LLM_ROUTE} \
    PI_RECON_CONCURRENCY=3 \
    PI_RECON_JOB_TIMEOUT_SEC=1200 \
    PI_RECON_SEGMENTS=2 \
    PI_RECON_JOBS_DIR=/app/jobs \
    DEEPSEEK_BASE_URL=${DEEPSEEK_BASE_URL} \
    LLM_ROUTE=${LLM_ROUTE} \
    TSEC_HOSTED=${TSEC_HOSTED}

# System + Node 22 (for pi)
# openjdk + binutils + binwalk + ncat: reverse / firmware / raw TCP helpers
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl xz-utils git unzip file \
      openjdk-17-jre-headless binutils binwalk ncat \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @earendil-works/pi-coding-agent@0.84.1 \
    && pi --version

# Linux recon tools — always fetch for TARGETARCH (hosted = linux/amd64).
RUN set -eux; \
    arch="${TARGETARCH:-amd64}"; \
    tmp=/tmp/tools; mkdir -p "$tmp" /opt/bin; \
    dl() { \
      url="$1"; out="$2"; \
      rm -f "$out"; \
      curl -fL --http1.1 --retry 8 --retry-delay 2 --retry-all-errors \
        --connect-timeout 30 --max-time 600 -o "$out" "$url"; \
      test -s "$out"; \
    }; \
    case "$arch" in \
      amd64|x86_64) \
        ffuf_url="https://github.com/ffuf/ffuf/releases/download/v2.1.0/ffuf_2.1.0_linux_amd64.tar.gz"; \
        ferox_url="https://github.com/epi052/feroxbuster/releases/download/v2.13.1/x86_64-linux-feroxbuster.zip"; \
        foundry_os_arch="linux_amd64"; \
        ;; \
      arm64|aarch64) \
        ffuf_url="https://github.com/ffuf/ffuf/releases/download/v2.1.0/ffuf_2.1.0_linux_arm64.tar.gz"; \
        ferox_url="https://github.com/epi052/feroxbuster/releases/download/v2.13.1/aarch64-linux-feroxbuster.zip"; \
        foundry_os_arch="linux_arm64"; \
        ;; \
      *) echo "unsupported TARGETARCH=$arch"; exit 1 ;; \
    esac; \
    dl "$ffuf_url" "$tmp/ffuf.tgz"; \
    tar -xzf "$tmp/ffuf.tgz" -C "$tmp"; \
    install -m 0755 "$tmp/ffuf" /opt/bin/ffuf; \
    file /opt/bin/ffuf | grep -qiE 'ELF|executable'; \
    dl "$ferox_url" "$tmp/ferox.zip"; \
    unzip -qo "$tmp/ferox.zip" -d "$tmp/ferox"; \
    ferox_bin="$(find "$tmp/ferox" -type f -name feroxbuster | head -1)"; \
    test -n "$ferox_bin"; \
    install -m 0755 "$ferox_bin" /opt/bin/feroxbuster; \
    # jadx (JVM — platform-independent zip)
    jadx_ver="1.5.1"; \
    dl "https://github.com/skylot/jadx/releases/download/v${jadx_ver}/jadx-${jadx_ver}.zip" "$tmp/jadx.zip"; \
    mkdir -p /opt/jadx; \
    unzip -qo "$tmp/jadx.zip" -d /opt/jadx; \
    if [ -x /opt/jadx/bin/jadx ]; then ln -sf /opt/jadx/bin/jadx /opt/bin/jadx; fi; \
    if [ -x /opt/jadx/bin/jadx-gui ]; then ln -sf /opt/jadx/bin/jadx-gui /opt/bin/jadx-gui; fi; \
    # cast only (foundry CLI)
    foundry_ver="1.7.1"; \
    foundry_url="https://github.com/foundry-rs/foundry/releases/download/v${foundry_ver}/foundry_v${foundry_ver}_${foundry_os_arch}.tar.gz"; \
    dl "$foundry_url" "$tmp/foundry.tgz"; \
    mkdir -p "$tmp/foundry"; \
    tar -xzf "$tmp/foundry.tgz" -C "$tmp/foundry"; \
    cast_bin="$(find "$tmp/foundry" -type f -name cast | head -1)"; \
    test -n "$cast_bin"; \
    install -m 0755 "$cast_bin" /opt/bin/cast; \
    if command -v ncat >/dev/null 2>&1; then \
      install -m 0755 "$(command -v ncat)" /opt/bin/ncat || ln -sf "$(command -v ncat)" /opt/bin/ncat; \
    fi; \
    rm -rf "$tmp"

# Python dependencies used by the reconnaissance tooling.
RUN pip install --no-cache-dir \
      arjun \
      pycryptodome \
      web3 \
      ldap3 \
      pyasn1 \
      pyasn1-modules

WORKDIR /app

# Explicit source only — never COPY . (keeps logs/jobs out of the image)
COPY platform_io.py agent_exec.py harvest.py net_llm.py loot_gate.py ./
COPY prompts ./prompts
COPY docker/entrypoint.sh ./docker/entrypoint.sh
COPY docker/${MODELS_FILE} ./docker/models.json

RUN chmod +x /app/docker/entrypoint.sh \
    && mkdir -p /app/jobs /app/tools/bin \
    && ln -sf /opt/bin/ffuf /app/tools/bin/ffuf \
    && ln -sf /opt/bin/feroxbuster /app/tools/bin/feroxbuster \
    && if [ -x /opt/bin/jadx ]; then ln -sf /opt/bin/jadx /app/tools/bin/jadx; fi \
    && if [ -x /opt/bin/jadx-gui ]; then ln -sf /opt/bin/jadx-gui /app/tools/bin/jadx-gui; fi \
    && if [ -x /opt/bin/cast ]; then ln -sf /opt/bin/cast /app/tools/bin/cast; fi \
    && if [ -x /opt/bin/ncat ]; then ln -sf /opt/bin/ncat /app/tools/bin/ncat; \
       elif command -v ncat >/dev/null 2>&1; then ln -sf "$(command -v ncat)" /app/tools/bin/ncat; fi \
    && if command -v binwalk >/dev/null 2>&1; then ln -sf "$(command -v binwalk)" /app/tools/bin/binwalk; fi \
    && if command -v strings >/dev/null 2>&1; then ln -sf "$(command -v strings)" /app/tools/bin/strings; fi \
    && printf '%s\n' '#!/usr/bin/env bash' 'exec python3 -m arjun "$@"' > /app/tools/bin/arjun \
    && printf '%s\n' '#!/usr/bin/env bash' 'exec python3 "$@"' > /app/tools/bin/python-tools \
    && chmod +x /app/tools/bin/arjun /app/tools/bin/python-tools \
    && mkdir -p /root/.pi/agent \
    && cp /app/docker/models.json /root/.pi/agent/models.json \
    && pi --version \
    && python3 -c "import platform_io, harvest, net_llm, agent_exec, loot_gate" \
    && ffuf -V | head -1 \
    && feroxbuster --version | head -1 \
    && (jadx --version 2>/dev/null | head -1 || true) \
    && (cast --version 2>/dev/null | head -1 || true) \
    && (binwalk -h 2>/dev/null | head -2 || true) \
    && (ncat --version 2>/dev/null | head -1 || true) \
    && (strings --version 2>/dev/null | head -1 || true) \
    && (arjun --help >/dev/null 2>&1 || python3 -m arjun --help >/dev/null 2>&1 || true) \
    && echo "tools:" && ls -la /app/tools/bin \
    && echo "baked models base:" && python3 -c "import json;print(json.load(open('/root/.pi/agent/models.json'))['providers']['deepseek']['baseUrl'])"

WORKDIR /app
ENTRYPOINT ["/app/docker/entrypoint.sh"]
