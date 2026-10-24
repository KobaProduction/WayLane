#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

# Pinned test runtime matching the Xray code imported from September 2026.
tag=v26.9.30
expected_sha256=f851110beaff16e78d643f0ccfd9524b4a44dfd59bae3e34bb52bba378f7690e
runtime="$PWD/.test-runtime/xray"
mkdir -p "$runtime"

if [[ ! -x "$runtime/xray" || ! -s "$runtime/geoip.dat" || ! -s "$runtime/geosite.dat" ]]; then
  command -v curl >/dev/null
  command -v unzip >/dev/null
  command -v sha256sum >/dev/null
  archive="$runtime/Xray-linux-64.zip"
  curl -fLsS --retry 2 --connect-timeout 10 --max-time 100 "https://github.com/XTLS/Xray-core/releases/download/$tag/Xray-linux-64.zip" -o "$archive"
  printf '%s  %s\n' "$expected_sha256" "$archive" | sha256sum -c -
  unzip -oq "$archive" 'xray' 'geoip.dat' 'geosite.dat' -d "$runtime"
  chmod +x "$runtime/xray"
  rm -f "$archive"
fi

mkdir -p certs
if [[ ! -s certs/ssl_cert.pem || ! -s certs/ssl_key.pem ]]; then
  command -v openssl >/dev/null
  openssl req -x509 -newkey rsa:2048 -nodes -keyout certs/ssl_key.pem \
    -out certs/ssl_cert.pem -subj '/CN=localhost' -days 1 \
    -addext 'subjectAltName=IP:127.0.0.1,DNS:localhost' \
    -addext 'basicConstraints=critical,CA:TRUE' >/dev/null 2>&1
  chmod 600 certs/ssl_key.pem
fi

printf 'Pinned Xray test runtime and local ephemeral TLS material ready.\n'
