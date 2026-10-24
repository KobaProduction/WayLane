#!/usr/bin/env python3
"""WayLane CLI"""

import typer

from cli import console
from cli.admin import generate_temp_key, init_owner

app = typer.Typer(
    name="WayLane",
    help="WayLane CLI",
    add_completion=False,
    rich_markup_mode="rich",
)


@app.command("generate-temp-key")
def cmd_generate_temp_key():
    """Generate a one-time temp key for owner setup (create/reset/delete)."""
    generate_temp_key()


@app.command("init-owner")
def cmd_init_owner(
    username: str = typer.Option("waylane_dev_owner", "--username", help="Local development owner login."),
    credentials_file: str = typer.Option(
        ".runtime/dev-owner-credentials.json",
        "--credentials-file",
        help="Write generated credentials to this new, private JSON file.",
    ),
):
    """Create the first owner only in local waylane_dev; refuse any other DB."""
    from pathlib import Path

    init_owner(username, Path(credentials_file))


@app.command()
def version():
    """Show WayLane version."""
    from app import __version__

    console.print(f"[bold blue]WayLane[/bold blue] version [bold green]{__version__}[/bold green]")


if __name__ == "__main__":
    app()
