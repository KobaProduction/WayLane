FROM alpine:3.23
COPY services/node/bootstrap/run-bootstrap.sh /usr/local/bin/waylane-bootstrap
RUN chmod 0755 /usr/local/bin/waylane-bootstrap
USER 0:0
ENTRYPOINT ["/usr/local/bin/waylane-bootstrap"]
