FROM alpine:3.23
COPY services/agent/bootstrap.sh /opt/waylane/run-bootstrap.sh
# Defensive normalisation if a Windows checkout ignored .gitattributes.
RUN sed -i 's/\r$//' /opt/waylane/run-bootstrap.sh
ENTRYPOINT ["/bin/sh", "/opt/waylane/run-bootstrap.sh"]
