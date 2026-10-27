FROM --platform=$BUILDPLATFORM golang:1.27.1-alpine AS build
ARG TARGETOS
ARG TARGETARCH
WORKDIR /src
COPY services/node/agent/go.mod ./go.mod
COPY services/node/agent/cmd ./cmd
RUN CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH go build -trimpath -o /out/waylane-agent ./cmd/agent

FROM alpine:3.23
RUN apk add --no-cache ca-certificates docker-cli
COPY --from=build /out/waylane-agent /usr/local/bin/waylane-agent
ENTRYPOINT ["/usr/local/bin/waylane-agent"]
