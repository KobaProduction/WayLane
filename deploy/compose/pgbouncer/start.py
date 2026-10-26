"""Run PgBouncer without a second credential or a committed auth file.

The userlist exists only in the container's private temporary directory and
is created anew at each startup. Do not log credentials or configuration.
"""
import os
from pathlib import Path

def main():
    username = os.environ.get("POSTGRES_USER", "")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    database = os.environ.get("POSTGRES_DB", "waylane")
    for name, value in (("POSTGRES_USER", username), ("POSTGRES_PASSWORD", password),
                        ("POSTGRES_DB", database)):
        if not value or "\n" in value or "\r" in value or "\0" in value:
            raise SystemExit(f"{name} must be nonempty and a single line")
    # PgBouncer auth_file quotes are escaped by doubling double quotes.
    user = username.replace('"', '""')
    secret = password.replace('"', '""')
    run = Path("/tmp/waylane-pgbouncer")
    run.mkdir(mode=0o700, exist_ok=True)
    os.chmod(run, 0o700)
    auth_file = run / "userlist.txt"
    auth_file.write_text(f'"{user}" "{secret}"\n')
    os.chmod(auth_file, 0o600)
    config = run / "pgbouncer.ini"
    config.write_text(
        "[databases]\n"
        "* = host=postgres port=5432\n"
        "[pgbouncer]\n"
        "listen_addr = 0.0.0.0\n"
        "listen_port = 5432\n"
        "auth_type = scram-sha-256\n"
        f"auth_file = {auth_file}\n"
        "pool_mode = session\n"
        "default_pool_size = 20\n"
        "max_client_conn = 100\n"
        "server_tls_sslmode = disable\n"
        "client_tls_sslmode = disable\n"
        "log_connections = 0\n"
        "log_disconnections = 0\n"
        "pidfile = /tmp/waylane-pgbouncer/pgbouncer.pid\n"
    )
    os.chmod(config, 0o600)
    os.execvp("pgbouncer", ["pgbouncer", str(config)])

if __name__ == "__main__":
    main()
