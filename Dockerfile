ARG PYTHON_VERSION=3.14

FROM ghcr.io/astral-sh/uv:python$PYTHON_VERSION-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    libc6-dev \
    && rm -rf /var/lib/apt/lists/*

ENV UV_PYTHON_DOWNLOADS=0

WORKDIR /build
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev
ADD . /build
# Required cross-repository UI artifact, supplied by waylane-frontend CI.
# Fail before the final image is produced instead of trying to build UI at runtime.
RUN test -f /build/dashboard/build/index.html && test -d /build/dashboard/build/statics
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:$PYTHON_VERSION-slim-bookworm

COPY --from=builder /build /code
WORKDIR /code

ENV PATH="/code/.venv/bin:$PATH"

# Keep the runtime trust store explicit. Outbound notification clients use it
# without replacing Python's process-wide SSLContext.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY cli_wrapper.sh /usr/bin/waylane-cli
RUN chmod +x /usr/bin/waylane-cli
# Temporary backwards-compatible entry point for existing operators.
RUN ln -s /usr/bin/waylane-cli /usr/bin/pasarguard-cli

COPY tui_wrapper.sh /usr/bin/waylane-tui
RUN chmod +x /usr/bin/waylane-tui
RUN ln -s /usr/bin/waylane-tui /usr/bin/pasarguard-tui

# Copy healthcheck script
COPY healthcheck.sh /code/healthcheck.sh
RUN chmod +x /code/healthcheck.sh

RUN chmod +x /code/start.sh

ENTRYPOINT ["/code/start.sh"]

# WayLane: the web bundle is produced in a separate repository.
# Install it using scripts/install_frontend.sh before building this image.
RUN test -f /code/dashboard/build/index.html && test -d /code/dashboard/build/statics
