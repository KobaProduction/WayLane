# Independent WayLane web frontend for Coolify Git builds and tagged releases.
FROM oven/bun:1.4.4-alpine AS builder
WORKDIR /app
COPY package.json bun.lock ./
RUN bun install --frozen-lockfile
COPY . .
# Existing public browser API base, configurable at image build time.
ARG VITE_BASE_API=/
ENV VITE_BASE_API=${VITE_BASE_API}
RUN bun run build
RUN test -f build/index.html && test -d build/statics

FROM nginxinc/nginx-unprivileged:1.28-alpine
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/build/ /usr/share/nginx/html/
EXPOSE 8080
HEALTHCHECK --interval=20s --timeout=5s --start-period=10s --retries=3 CMD wget -q -O /dev/null http://127.0.0.1:8080/health || exit 1
