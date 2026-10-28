#!/usr/bin/env python3
"""
WayLane CLI Wrapper
This script provides a simple entry point for the WayLane CLI.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    # docker exec does not inherit the SQLALCHEMY_DATABASE_URL dynamically
    # assembled inside the already-running API process.
    if not os.environ.get("SQLALCHEMY_DATABASE_URL") and any(
        os.environ.get(name) for name in ("POSTGRES_HOST", "POSTGRES_USER", "POSTGRES_PASSWORD")
    ):
        from compose_db_entrypoint import configure_database_url_from_components

        configure_database_url_from_components()

    from cli.main import app

    if len(sys.argv) == 1:
        sys.argv.append("--help")
    app()
except ImportError as e:
    print(f"Error importing CLI: {e}")
    print("Make sure you're running this from the WayLane project directory.")
    sys.exit(1)
except Exception as e:
    print(f"Error running CLI: {e}")
    sys.exit(1)
