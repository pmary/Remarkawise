"""Sync state management using SQLite.

Tracks which documents have been synced and their versions to enable
incremental synchronization.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, TypedDict

from remarkawise.models import Highlight, SyncState
from remarkawise.utils import generate_content_checksum, generate_text_hash


class SyncedHighlightInfo(TypedDict):
    """Information about a synced highlight."""

    highlight_id: str
    text_hash: str
    readwise_id: Optional[int]


class SyncStats(TypedDict):
    """Sync statistics."""

    documents_synced: int
    highlights_synced: int
    total_highlights: int


class StateManager:
    """Manages sync state persistence using SQLite."""

    def __init__(self, db_path: Path) -> None:
        """Initialize the state manager.

        Args:
            db_path: Path to the SQLite database file
        """
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._ensure_schema()

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create database connection."""
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _ensure_schema(self) -> None:
        """Create database tables if they don't exist."""
        conn = self._get_conn()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sync_state (
                document_id TEXT PRIMARY KEY,
                document_name TEXT NOT NULL,
                last_synced_at TEXT NOT NULL,
                last_version INTEGER NOT NULL,
                highlight_count INTEGER DEFAULT 0,
                checksum TEXT
            );

            CREATE TABLE IF NOT EXISTS synced_highlights (
                highlight_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                text_hash TEXT NOT NULL,
                synced_at TEXT NOT NULL,
                readwise_id INTEGER,
                FOREIGN KEY (document_id) REFERENCES sync_state(document_id)
            );

            CREATE INDEX IF NOT EXISTS idx_synced_highlights_doc
            ON synced_highlights(document_id);
        """
        )
        conn.commit()

        # Run migrations for existing databases
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        """Run schema migrations for existing databases."""
        conn = self._get_conn()

        # Check if readwise_id column exists
        cursor = conn.execute("PRAGMA table_info(synced_highlights)")
        columns = {row[1] for row in cursor.fetchall()}

        if "readwise_id" not in columns:
            conn.execute(
                "ALTER TABLE synced_highlights ADD COLUMN readwise_id INTEGER"
            )
            conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def get_sync_state(self, document_id: str) -> Optional[SyncState]:
        """Get the sync state for a document.

        Args:
            document_id: Document ID to look up

        Returns:
            SyncState if document has been synced, None otherwise
        """
        conn = self._get_conn()
        cursor = conn.execute(
            "SELECT * FROM sync_state WHERE document_id = ?",
            (document_id,),
        )
        row = cursor.fetchone()

        if not row:
            return None

        return SyncState(
            document_id=row["document_id"],
            document_name=row["document_name"],
            last_synced_at=datetime.fromisoformat(row["last_synced_at"]),
            last_version=row["last_version"],
            highlight_count=row["highlight_count"],
            checksum=row["checksum"],
        )

    def update_sync_state(self, state: SyncState) -> None:
        """Update or create sync state for a document.

        Args:
            state: SyncState to save
        """
        conn = self._get_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO sync_state
            (document_id, document_name, last_synced_at, last_version, highlight_count, checksum)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                state.document_id,
                state.document_name,
                state.last_synced_at.isoformat(),
                state.last_version,
                state.highlight_count,
                state.checksum,
            ),
        )
        conn.commit()

    def is_highlight_synced(self, highlight_id: str) -> bool:
        """Check if a highlight has already been synced.

        Args:
            highlight_id: Highlight ID to check

        Returns:
            True if already synced
        """
        conn = self._get_conn()
        cursor = conn.execute(
            "SELECT 1 FROM synced_highlights WHERE highlight_id = ?",
            (highlight_id,),
        )
        return cursor.fetchone() is not None

    def mark_highlights_synced(
        self,
        document_id: str,
        highlights: list[Highlight],
        readwise_ids: Optional[dict[str, int]] = None,
    ) -> None:
        """Mark multiple highlights as synced.

        Args:
            document_id: Document the highlights belong to
            highlights: List of highlights to mark
            readwise_ids: Optional mapping of highlight text hash to Readwise ID
        """
        conn = self._get_conn()
        now = datetime.utcnow().isoformat()
        readwise_ids = readwise_ids or {}

        for highlight in highlights:
            text_hash = generate_text_hash(highlight.text)
            readwise_id = readwise_ids.get(text_hash)
            conn.execute(
                """
                INSERT OR REPLACE INTO synced_highlights
                (highlight_id, document_id, text_hash, synced_at, readwise_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (highlight.id, document_id, text_hash, now, readwise_id),
            )

        conn.commit()

    def get_unsynced_highlights(
        self, document_id: str, highlights: list[Highlight]
    ) -> list[Highlight]:
        """Filter to only highlights that haven't been synced.

        Args:
            document_id: Document ID
            highlights: List of all highlights from document

        Returns:
            List of highlights not yet synced
        """
        return [h for h in highlights if not self.is_highlight_synced(h.id)]

    def get_synced_highlights_for_document(
        self, document_id: str
    ) -> list[SyncedHighlightInfo]:
        """Get all synced highlights for a document.

        Args:
            document_id: Document ID

        Returns:
            List of SyncedHighlightInfo with highlight_id, text_hash, and readwise_id
        """
        conn = self._get_conn()
        cursor = conn.execute(
            """
            SELECT highlight_id, text_hash, readwise_id
            FROM synced_highlights
            WHERE document_id = ?
            """,
            (document_id,),
        )

        return [
            {
                "highlight_id": row["highlight_id"],
                "text_hash": row["text_hash"],
                "readwise_id": row["readwise_id"],
            }
            for row in cursor.fetchall()
        ]

    def get_deleted_highlights(
        self, document_id: str, current_highlights: list[Highlight]
    ) -> list[SyncedHighlightInfo]:
        """Find highlights that were synced but are no longer on the device.

        Args:
            document_id: Document ID
            current_highlights: List of highlights currently on the device

        Returns:
            List of SyncedHighlightInfo for highlights that were deleted from device
        """
        current_ids = {h.id for h in current_highlights}
        synced = self.get_synced_highlights_for_document(document_id)

        return [h for h in synced if h["highlight_id"] not in current_ids]

    def remove_highlights(self, highlight_ids: list[str]) -> None:
        """Remove highlights from sync tracking.

        Args:
            highlight_ids: List of highlight IDs to remove
        """
        if not highlight_ids:
            return

        conn = self._get_conn()
        placeholders = ",".join("?" * len(highlight_ids))
        conn.execute(
            f"DELETE FROM synced_highlights WHERE highlight_id IN ({placeholders})",
            highlight_ids,
        )
        conn.commit()

    def needs_sync(self, document_id: str, current_version: int) -> bool:
        """Check if a document needs to be synced.

        Args:
            document_id: Document ID to check
            current_version: Current document version

        Returns:
            True if document needs sync (new or updated)
        """
        state = self.get_sync_state(document_id)

        if state is None:
            return True

        return current_version > state.last_version

    def calculate_highlights_checksum(self, highlights: list[Highlight]) -> str:
        """Calculate a checksum for a list of highlights.

        Used to detect changes in highlights without re-uploading.

        Args:
            highlights: List of highlights

        Returns:
            Checksum string
        """
        content = json.dumps(
            [{"id": h.id, "text": h.text, "page": h.page_number} for h in highlights],
            sort_keys=True,
        )
        return generate_content_checksum(content)

    def get_all_synced_documents(self) -> list[SyncState]:
        """Get all documents that have been synced.

        Returns:
            List of SyncState objects
        """
        conn = self._get_conn()
        cursor = conn.execute("SELECT * FROM sync_state ORDER BY last_synced_at DESC")

        states = []
        for row in cursor:
            states.append(
                SyncState(
                    document_id=row["document_id"],
                    document_name=row["document_name"],
                    last_synced_at=datetime.fromisoformat(row["last_synced_at"]),
                    last_version=row["last_version"],
                    highlight_count=row["highlight_count"],
                    checksum=row["checksum"],
                )
            )

        return states

    def get_stats(self) -> SyncStats:
        """Get sync statistics.

        Returns:
            SyncStats with documents_synced, highlights_synced, total_highlights
        """
        conn = self._get_conn()

        doc_count = conn.execute("SELECT COUNT(*) FROM sync_state").fetchone()[0]
        highlight_count = conn.execute(
            "SELECT COUNT(*) FROM synced_highlights"
        ).fetchone()[0]
        total_highlights = conn.execute(
            "SELECT SUM(highlight_count) FROM sync_state"
        ).fetchone()[0]

        return {
            "documents_synced": doc_count,
            "highlights_synced": highlight_count,
            "total_highlights": total_highlights or 0,
        }

    def reset_document(self, document_id: str) -> bool:
        """Reset sync state for a specific document.

        Args:
            document_id: Document ID to reset

        Returns:
            True if document was found and reset, False if not found
        """
        conn = self._get_conn()

        # Check if document exists
        cursor = conn.execute(
            "SELECT 1 FROM sync_state WHERE document_id = ?",
            (document_id,),
        )
        if cursor.fetchone() is None:
            return False

        # Delete highlights first (foreign key)
        conn.execute(
            "DELETE FROM synced_highlights WHERE document_id = ?",
            (document_id,),
        )
        # Delete document state
        conn.execute(
            "DELETE FROM sync_state WHERE document_id = ?",
            (document_id,),
        )
        conn.commit()
        return True

    def reset_all(self) -> int:
        """Reset all sync state.

        Returns:
            Number of documents that were reset
        """
        conn = self._get_conn()

        # Get count before deletion
        doc_count = conn.execute("SELECT COUNT(*) FROM sync_state").fetchone()[0]

        # Delete all data
        conn.execute("DELETE FROM synced_highlights")
        conn.execute("DELETE FROM sync_state")
        conn.commit()

        return doc_count
