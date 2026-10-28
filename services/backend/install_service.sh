#!/bin/bash

SERVICE_NAME="waylane"
SERVICE_DESCRIPTION="WayLane Service"
SERVICE_DOCUMENTATION="https://github.com/KobaProduction/waylane/tree/main/services/backend"
MAIN_PY_PATH="$PWD/main.py"
SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME.service"

# Create the service file
cat > $SERVICE_FILE <<EOF
[Unit]
Description=$SERVICE_DESCRIPTION
Documentation=$SERVICE_DOCUMENTATION
After=network.target nss-lookup.target

[Service]
ExecStart=$PWD/.venv/bin/python3 $MAIN_PY_PATH
Restart=on-failure
WorkingDirectory=$PWD

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload

echo "Service file created at: $SERVICE_FILE"
