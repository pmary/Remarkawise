"""Command-line interface for Remarkawise.

This module provides the CLI for syncing highlights from reMarkable to Readwise.

Commands:
    sync           - Sync highlights from reMarkable to Readwise
    status         - Show sync status and statistics
    auth           - Verify Readwise authentication
    list-documents - List documents from reMarkable local cache
    reset          - Reset sync state to re-sync highlights

Error Handling:
    - Configuration errors (missing tokens) exit with code 1
    - API errors are caught and displayed with context
    - All commands support --verbose for debug output

Example Usage:
    # Sync all documents
    remarkawise sync

    # Sync only documents tagged 'readwise'
    remarkawise sync --tag readwise

    # Sync with AI text cleanup
    remarkawise sync --llm-cleanup --verbose

    # Check authentication
    remarkawise auth
"""

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from remarkawise import __version__
from remarkawise.config import Settings, settings
from remarkawise.logging import setup_logging
from remarkawise.readwise.client import ReadwiseClient
from remarkawise.remarkable.local_cache import LocalCacheClient, LocalCacheError
from remarkawise.sync.engine import SyncEngine
from remarkawise.sync.state import StateManager

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
    tag: Optional[str] = typer.Option(
        None,
        "--tag",
        "-t",
        help="Sync only documents with this tag (e.g., 'readwise').",
    ),
    llm_cleanup: bool = typer.Option(
        False,
        "--llm-cleanup",
        help="Use Claude AI to fix corrupted text from PDF extraction. Requires ANTHROPIC_API_KEY.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show detailed debug information during sync.",
    ),
) -> None:
    """Sync highlights from reMarkable to Readwise.

    Extracts highlights from reMarkable documents (via desktop app cache)
    and uploads them to Readwise.

    Use --tag to filter by document tags (e.g., --tag=readwise to only sync
    documents tagged with 'readwise' on your reMarkable).

    Use --llm-cleanup to fix corrupted text using Claude AI (requires ANTHROPIC_API_KEY
    in your .env file). This fixes issues like missing spaces, broken ligatures, etc.
    """
    if not settings.readwise_access_token:
        console.print(
            "[red]Error:[/red] Readwise not configured. "
            "Set READWISE_ACCESS_TOKEN in your .env file."
        )
        raise typer.Exit(1)

    # Validate LLM cleanup configuration
    if llm_cleanup and not settings.anthropic_api_key:
        console.print(
            "[red]Error:[/red] --llm-cleanup requires ANTHROPIC_API_KEY. "
            "Add it to your .env file."
        )
        raise typer.Exit(1)

    # Setup logging for verbose/debug output
    if verbose:
        setup_logging(verbose=True)

    tag_info = f" (filtering by tag: '{tag}')" if tag else ""
    llm_info = " with LLM cleanup" if llm_cleanup else ""
    console.print(f"[bold]Starting sync{tag_info}{llm_info}...[/bold]")

    try:
        # Create settings copy
        sync_settings = Settings(
            remarkable_local_cache_path=settings.remarkable_local_cache_path,
            readwise_access_token=settings.readwise_access_token,
            anthropic_api_key=settings.anthropic_api_key,
            sync_interval_minutes=settings.sync_interval_minutes,
            sync_folders=settings.sync_folders,
            data_dir=settings.data_dir,
        )
        engine = SyncEngine(sync_settings, verbose=verbose, llm_cleanup=llm_cleanup)
        document_ids = [document] if document else None
        filter_tag = tag

        if verbose:
            result = engine.sync(force=force, document_ids=document_ids, tag=filter_tag)
        else:
            with console.status("[bold green]Syncing highlights..."):
                result = engine.sync(force=force, document_ids=document_ids, tag=filter_tag)

        # Get total stats before closing
        total_stats = engine.get_status()
        engine.close()

        if result.success:
            stats = (
                f"\n[green]Sync completed successfully![/green]\n"
                f"  Documents processed: {result.documents_processed}\n"
                f"  Highlights synced: {result.highlights_synced}"
            )
            if result.highlights_deleted > 0:
                stats += f"\n  Highlights deleted: {result.highlights_deleted}"
            total_count = total_stats['stats']['highlights_synced']
            if document:
                stats += f"\n  Total highlights tracked: {total_count} (across all documents)"
            else:
                stats += f"\n  Total highlights tracked: {total_count}"
            console.print(stats)
        else:
            console.print("\n[yellow]Sync completed with errors:[/yellow]")
            for error in result.errors:
                console.print(f"  [red]- {error}[/red]")

            stats = (
                f"\n  Documents processed: {result.documents_processed}\n"
                f"  Highlights synced: {result.highlights_synced}"
            )
            if result.highlights_deleted > 0:
                stats += f"\n  Highlights deleted: {result.highlights_deleted}"
            total_count = total_stats['stats']['highlights_synced']
            if document:
                stats += f"\n  Total highlights tracked: {total_count} (across all documents)"
            else:
                stats += f"\n  Total highlights tracked: {total_count}"
            console.print(stats)

    except Exception as e:
        console.print(f"[red]Error:[/red] {str(e)}")
        raise typer.Exit(1)


@app.command()
def status() -> None:
    """Show sync status and statistics.

    Displays a summary of sync activity including:
    - Total documents synced
    - Total highlights tracked
    - Recent sync history with document names and highlight counts

    The data is read from the local sync state database.
    """
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

            for sync_item in recent:
                table.add_row(
                    sync_item["name"][:40],
                    sync_item["synced_at"][:19],
                    str(sync_item["highlights"]),
                )

            console.print(table)
        else:
            console.print("\n[dim]No documents synced yet.[/dim]")

    except Exception as e:
        console.print(f"[red]Error:[/red] {str(e)}")
        raise typer.Exit(1)


@app.command()
def auth() -> None:
    """Verify Readwise authentication.

    Validates your Readwise access token.
    """
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
def list_documents(
    all_types: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Show all document types, not just PDFs.",
    ),
    full_id: bool = typer.Option(
        False,
        "--full-id",
        help="Show full document IDs (useful for --document flag).",
    ),
) -> None:
    """List all documents from reMarkable local cache.

    Reads document metadata from the reMarkable desktop app's local cache
    directory. By default, only shows syncable document types (PDF, EPUB).

    The output includes document ID, name, tags, and modification date.
    Use --full-id to get complete document IDs for use with other commands.

    Args:
        all_types: Show all document types including notebooks
        full_id: Display complete document IDs instead of truncated versions
    """
    try:
        with console.status("[bold green]Fetching documents from local cache..."):
            client = LocalCacheClient(settings.remarkable_local_cache_path)
            documents = client.list_documents()
            client.close()

        if not documents:
            console.print("[dim]No documents found.[/dim]")
            return

        # Filter to syncable types (PDF and EPUB) unless --all is specified
        if all_types:
            filtered_docs = documents
        else:
            syncable_types = {"pdf", "epub"}
            filtered_docs = [d for d in documents if d.document_type.value in syncable_types]

        doc_type_label = "documents" if all_types else "syncable documents (PDF/EPUB)"
        console.print(f"\n[bold]Found {len(filtered_docs)} {doc_type_label}:[/bold]\n")

        table = Table()
        table.add_column("ID", style="dim", no_wrap=full_id)
        table.add_column("Name", style="cyan")
        if all_types:
            table.add_column("Type")
        table.add_column("Tags", style="yellow")
        table.add_column("Modified")

        for doc in filtered_docs:
            doc_id_display = doc.id if full_id else doc.id[:8] + "..."
            tags_display = ", ".join(doc.tags) if doc.tags else "-"
            row = [
                doc_id_display,
                doc.name[:50],
            ]
            if all_types:
                row.append(doc.document_type.value)
            row.append(tags_display)
            row.append(doc.modified_time.strftime("%Y-%m-%d %H:%M"))
            table.add_row(*row)

        console.print(table)

        if not full_id:
            console.print(
                "\n[dim]Tip: Use --full-id to show complete document IDs "
                "for use with --document.[/dim]"
            )
            console.print("[dim]Tip: Use --tag=<name> with sync to filter by tag.[/dim]")

    except LocalCacheError as e:
        console.print(f"[red]Error:[/red] {str(e)}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {str(e)}")
        raise typer.Exit(1)


@app.command()
def reset(
    document: Optional[str] = typer.Option(
        None,
        "--document",
        "-d",
        help="Reset sync state for a specific document by ID. "
        "Use 'remarkawise list-documents --full-id' to find document IDs.",
    ),
    all_docs: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Reset sync state for ALL documents. This will cause all highlights "
        "to be re-synced on the next sync.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip confirmation prompt (use with caution).",
    ),
) -> None:
    """Reset sync state to re-sync highlights.

    This command clears the local sync tracking database, which allows highlights
    to be synced again. This is useful when:

    \b
    - You want to re-upload all highlights to Readwise
    - The sync state has become corrupted or out of sync
    - You've manually deleted highlights from Readwise and want to re-sync them

    \b
    IMPORTANT NOTES:
    - This does NOT delete highlights from Readwise
    - This does NOT delete highlights from your reMarkable
    - This only clears the local tracking of what has been synced
    - After reset, running 'sync' will re-upload all highlights as new

    \b
    EXAMPLES:
        # Reset a specific document (get ID from list-documents --full-id)
        remarkawise reset --document 1d56bc1c-5ed1-45eb-9c45-e7e00be73973

        # Reset all documents (will prompt for confirmation)
        remarkawise reset --all

        # Reset all without confirmation (for scripts)
        remarkawise reset --all --yes
    """
    # Validate options
    if not document and not all_docs:
        console.print(
            "[red]Error:[/red] You must specify either --document or --all.\n\n"
            "Examples:\n"
            "  remarkawise reset --document <document-id>\n"
            "  remarkawise reset --all"
        )
        raise typer.Exit(1)

    if document and all_docs:
        console.print(
            "[red]Error:[/red] Cannot use both --document and --all. "
            "Choose one option."
        )
        raise typer.Exit(1)

    try:
        state_manager = StateManager(settings.sync_state_path)

        if document:
            # Reset specific document
            existing = state_manager.get_sync_state(document)
            if not existing:
                console.print(
                    f"[yellow]No sync state found for document:[/yellow] {document}\n\n"
                    "This document may have never been synced, or the ID may be incorrect.\n"
                    "Use 'remarkawise list-documents --full-id' to find valid document IDs."
                )
                state_manager.close()
                raise typer.Exit(1)

            # Show what will be reset
            console.print("\n[bold]Document to reset:[/bold]")
            console.print(f"  Name: {existing.document_name}")
            console.print(f"  ID: {document}")
            console.print(f"  Highlights tracked: {existing.highlight_count}")
            console.print(f"  Last synced: {existing.last_synced_at.strftime('%Y-%m-%d %H:%M')}")

            if not yes:
                confirm = typer.confirm(
                    "\nReset sync state for this document? "
                    "(Highlights will be re-synced on next sync)"
                )
                if not confirm:
                    console.print("[dim]Cancelled.[/dim]")
                    state_manager.close()
                    raise typer.Exit(0)

            state_manager.reset_document(document)
            state_manager.close()

            console.print(
                f"\n[green]Reset complete![/green] "
                f"Sync state cleared for '{existing.document_name}'.\n"
                f"Run 'remarkawise sync --document {document}' to re-sync highlights."
            )

        else:
            # Reset all documents
            stats = state_manager.get_stats()
            doc_count = stats["documents_synced"]
            highlight_count = stats["highlights_synced"]

            if doc_count == 0:
                console.print("[dim]No sync state to reset. Nothing has been synced yet.[/dim]")
                state_manager.close()
                raise typer.Exit(0)

            console.print("\n[bold yellow]Warning: This will reset ALL sync state![/bold yellow]")
            console.print(f"\n  Documents tracked: {doc_count}")
            console.print(f"  Highlights tracked: {highlight_count}")
            console.print(
                "\n[dim]This will NOT delete highlights from Readwise, "
                "but will cause all highlights to be re-uploaded on next sync.[/dim]"
            )

            if not yes:
                confirm = typer.confirm(
                    "\nAre you sure you want to reset ALL sync state?"
                )
                if not confirm:
                    console.print("[dim]Cancelled.[/dim]")
                    state_manager.close()
                    raise typer.Exit(0)

            reset_count = state_manager.reset_all()
            state_manager.close()

            console.print(
                f"\n[green]Reset complete![/green] "
                f"Cleared sync state for {reset_count} document(s).\n"
                "Run 'remarkawise sync' to re-sync all highlights."
            )

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error:[/red] {str(e)}")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
