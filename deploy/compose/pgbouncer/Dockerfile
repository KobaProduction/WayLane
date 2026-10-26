# Reproducible pooler package without an untrusted shell download.
FROM alpine:3.22
RUN apk add --no-cache pgbouncer python3 &&     addgroup -S waylane && adduser -S -D -H -G waylane -u 10001 waylane
COPY start.py /usr/local/bin/waylane-pgbouncer.py
USER 10001:10001
EXPOSE 5432
ENTRYPOINT ["python3", "/usr/local/bin/waylane-pgbouncer.py"]
