FROM --platform=$BUILDPLATFORM golang:1.27.1-alpine AS build
ARG TARGETOS
ARG TARGETARCH
WORKDIR /src
COPY services/node/go.mod services/node/go.sum ./
COPY services/node/cmd/agent ./cmd/agent
RUN CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH go build -trimpath -o /out/waylane-agent ./cmd/agent

# Official RootlessKit + dockerd + CLI IN SAME agent container.
FROM docker:29.5-dind-rootless
COPY --from=build --chown=1000:1000 /out/waylane-agent /usr/local/bin/waylane-agent
USER 1000:1000
ENTRYPOINT ["/usr/local/bin/waylane-agent"]
