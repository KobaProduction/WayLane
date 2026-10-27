# One-shot host preflight and provisioner; never used as the VPN agent.
FROM alpine:3.23
RUN apk add --no-cache bash util-linux coreutils
COPY services/node/bootstrap/run-bootstrap.sh /opt/waylane/run-bootstrap.sh
COPY services/node/bootstrap/bootstrap-host.sh /opt/waylane/bootstrap-host.sh
ENTRYPOINT ["/bin/bash", "/opt/waylane/run-bootstrap.sh"]
