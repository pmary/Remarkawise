"""Command-line interface for Remarkawise."""

import sys
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from remarkawise import __version__
from remarkawise.config import Settings, settings
from remarkawise.readwise.client import ReadwiseClient
from remarkawise.remarkable.client import RemarkableClient
from remarkawise.sync.engine import SyncEngine

app = typer.Typer(
    name="remarkawise",
    help="Sync highlighted PDFs from reMarkable Paper Pro to Readwise Reader.",
    add_completion=False,
)
console = Console()


def version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        console.print(f"Remarkawise v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        callback=version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Remarkawise - Sync reMarkable highlights to Readwise."""
    pass


@app.command()
def sync(
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force re-sync all documents, ignoring previous sync state.",
    ),
    document: Optional[str] = typer.Option(
        None,
        "--document",
        "-d",
        help="Sync only a specific document by ID.",
    ),
) -> None:
    """Sync highlights from reMarkable to Readwise.

    Downloads PDFs from reMarkable Cloud, extracts highlights,
    and uploads them to your Readwise account.
    """
    # Validate configuration
    if not settings.remarkable_device_token:
        console.print(
            "[red]Error:[/red] reMarkable not configured. "
            "Run 'remarkawise auth remarkable' first."
        )
        raise typer.Exit(1)

    if not settings.readwise_access_token:
        console.print(
            "[red]Error:[/red] Readwise not configured. "
            "Set READWISE_ACCESS_TOKEN in your .env file."
        )
        raise typer.Exit(1)

    console.print("[bold]Starting sync...[/bold]")

    try:
        engine = SyncEngine(settings)
        document_ids = [document] if document else None

        with console.status("[bold green]Syncing highlights..."):
            result = engine.sync(force=force, document_ids=document_ids)

        engine.close()

        if result.success:
            console.print(
                f"\n[green]Sync completed successfully![/green]\n"
                f"  Documents processed: {result.documents_processed}\n"
                f"  Highlights synced: {result.highlights_synced}"
            )
        else:
            console.print(f"\n[yellow]Sync completed with errors:[/yellow]")
            for error in result.errors:
                console.print(f"  [red]- {error}[/red]")

            console.print(
                f"\n  Documents processed: {result.documents_processed}\n"
                f"  Highlights synced: {result.highlights_synced}"
            )

    except Exception as e:
        console.print(f"[red]Error:[/red] {str(e)}")
        raise typer.Exit(1)


@app.command()
def status() -> None:
    """Show sync status and statistics."""
    try:
        engine = SyncEngine(settings)
        status_data = engine.get_status()
        engine.close()

        stats = status_data["stats"]
        console.print("\n[bold]Sync Statistics[/bold]")
        console.print(f"  Documents synced: {stats['documents_synced']}")
        console.print(f"  Total highlights: {stats['total_highlights']}")

        recent = status_data["recent_syncs"]
        if recent:
            console.print("\n[bold]Recent Syncs[/bold]")
            table = Table()
            table.add_column("Document", style="cyan")
            table.add_column("Synced At")
            table.add_column("Highlights", justify="right")

            for sync in recent:
                table.add_row(
                    sync["name"][:40],
                    sync["synced_at"][:19],
                    str(sync["highlights"]),
                )

            console.print(table)
        else:
            console.print("\n[dim]No documents synced yet.[/dim]")

    except Exception as e:
        console.print(f"[red]Error:[/red] {str(e)}")
        raise typer.Exit(1)


@app.command()
def auth(
    service: str = typer.Argument(
        ...,
        help="Service to authenticate: 'remarkable' or 'readwise'",
    ),
) -> None:
    """Configure authentication for reMarkable or Readwise.

    For reMarkable: Opens the registration flow to get a device token.
    For Readwise: Validates your access token.
    """
    if service.lower() == "remarkable":
        _auth_remarkable()
    elif service.lower() == "readwise":
        _auth_readwise()
    else:
        console.print(
            f"[red]Unknown service:[/red] {service}\n"
            "Use 'remarkable' or 'readwise'."
        )
        raise typer.Exit(1)


def _auth_remarkable() -> None:
    """Handle reMarkable authentication."""
    console.print("\n[bold]reMarkable Cloud Authentication[/bold]\n")
    console.print("1. Go to: [link]https://my.remarkable.com/device/desktop/connect[/link]")
    console.print("2. Enter the one-time code displayed on the website\n")

    code = typer.prompt("Enter the one-time code")

    try:
        with console.status("[bold green]Registering device..."):
            client = RemarkableClient()
            # Note: This is sync for simplicity in CLI
            import asyncio

            token = asyncio.run(client.register_device(code))
            client.close()

        console.print("\n[green]Successfully registered![/green]")
        console.print(f"\nAdd this to your .env file:")
        console.print(f"[cyan]REMARKABLE_DEVICE_TOKEN={token}[/cyan]")

    except Exception as e:
        console.print(f"\n[red]Registration failed:[/red] {str(e)}")
        raise typer.Exit(1)


def _auth_readwise() -> None:
    """Handle Readwise authentication."""
    console.print("\n[bold]Readwise Authentication[/bold]\n")

    if not settings.readwise_access_token:
        console.print(
            "No Readwise token found. Get your token from:\n"
            "[link]https://readwise.io/access_token[/link]\n\n"
            "Then add it to your .env file:\n"
            "[cyan]READWISE_ACCESS_TOKEN=your_token_here[/cyan]"
        )
        raise typer.Exit(1)

    try:
        with console.status("[bold green]Verifying token..."):
            client = ReadwiseClient(settings.readwise_access_token)
            valid = client.verify_token()
            client.close()

        if valid:
            console.print("[green]Readwise token is valid![/green]")
        else:
            console.print("[red]Readwise token is invalid.[/red]")
            console.print("Get a new token from: https://readwise.io/access_token")
            raise typer.Exit(1)

    except Exception as e:
        console.print(f"[red]Verification failed:[/red] {str(e)}")
        raise typer.Exit(1)


@app.command()
def list_documents() -> None:
    """List all documents in your reMarkable cloud."""
    if not settings.remarkable_device_token:
        console.print(
            "[red]Error:[/red] reMarkable not configured. "
            "Run 'remarkawise auth remarkable' first."
        )
        raise typer.Exit(1)

    try:
        with console.status("[bold green]Fetching documents..."):
            client = RemarkableClient(settings.remarkable_device_token)
            documents = client.list_documents()
            client.close()

        if not documents:
            console.print("[dim]No documents found.[/dim]")
            return

        # Filter to PDFs
        pdf_docs = [d for d in documents if d.document_type.value == "pdf"]

        console.print(f"\n[bold]Found {len(pdf_docs)} PDF documents:[/bold]\n")

        table = Table()
        table.add_column("ID", style="dim")
        table.add_column("Name", style="cyan")
        table.add_column("Modified")

        for doc in pdf_docs:
            table.add_row(
                doc.id[:8] + "...",
                doc.name[:50],
                doc.modified_time.strftime("%Y-%m-%d %H:%M"),
            )

        console.print(table)

    except Exception as e:
        console.print(f"[red]Error:[/red] {str(e)}")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
