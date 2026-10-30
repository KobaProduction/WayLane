FROM --platform=$BUILDPLATFORM golang:1.27.1-alpine AS agent-build
ARG TARGETOS
ARG TARGETARCH
WORKDIR /src
COPY services/agent/go.mod ./
COPY services/agent/*.go ./
RUN CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH go build -trimpath -o /out/waylane-agent .

FROM --platform=$BUILDPLATFORM golang:1.27.1-alpine AS node-seed-build
ARG TARGETOS
ARG TARGETARCH
WORKDIR /src
COPY services/node/go.mod services/node/go.sum ./
COPY services/node/*.go ./
RUN CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH go build -trimpath -o /out/waylane-node .

FROM docker:29.5-dind-rootless
COPY --from=agent-build --chown=1000:1000 /out/waylane-agent /usr/local/bin/waylane-agent
COPY --from=node-seed-build --chown=1000:1000 /out/waylane-node /opt/waylane/node-seed/waylane-node
COPY --chown=1000:1000 services/agent/node-seed.Dockerfile /opt/waylane/node-seed/Dockerfile
USER 1000:1000
ENTRYPOINT ["/usr/local/bin/waylane-agent"]
