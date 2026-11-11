FROM docker:29.5-cli
RUN mkdir -p /etc/waylane /run/user/1000 /var/lib/waylane-agent
COPY --chown=1000:1000 waylane-node /usr/local/bin/waylane-node
USER 1000:1000
ENTRYPOINT ["/usr/local/bin/waylane-node"]
