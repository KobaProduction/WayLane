FROM --platform=$BUILDPLATFORM golang:1.27.1-alpine AS build
ARG TARGETOS
ARG TARGETARCH
WORKDIR /src
COPY services/node/go.mod services/node/go.sum ./
COPY services/node/*.go ./
RUN CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH go build -trimpath -o /out/waylane-node .

# Replaceable Node: Docker CLI only. Stable Agent owns RootlessKit/dockerd.
FROM docker:29.5-cli
RUN mkdir -p /etc/waylane /run/user/1000 /var/lib/waylane-agent
COPY --from=build --chown=1000:1000 /out/waylane-node /usr/local/bin/waylane-node
USER 1000:1000
ENTRYPOINT ["/usr/local/bin/waylane-node"]
