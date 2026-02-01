"""Sync state management using SQLite.

Tracks which documents have been synced and their versions to enable
incremental synchronization.
"""

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from remarkawise.models import Highlight, SyncState


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
                FOREIGN KEY (document_id) REFERENCES sync_state(document_id)
            );

            CREATE INDEX IF NOT EXISTS idx_synced_highlights_doc
            ON synced_highlights(document_id);
        """
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
        self, document_id: str, highlights: list[Highlight]
    ) -> None:
        """Mark multiple highlights as synced.

        Args:
            document_id: Document the highlights belong to
            highlights: List of highlights to mark
        """
        conn = self._get_conn()
        now = datetime.utcnow().isoformat()

        for highlight in highlights:
            text_hash = hashlib.sha256(highlight.text.encode()).hexdigest()[:32]
            conn.execute(
                """
                INSERT OR REPLACE INTO synced_highlights
                (highlight_id, document_id, text_hash, synced_at)
                VALUES (?, ?, ?, ?)
                """,
                (highlight.id, document_id, text_hash, now),
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
        return hashlib.sha256(content.encode()).hexdigest()[:32]

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

    def get_stats(self) -> dict[str, int]:
        """Get sync statistics.

        Returns:
            Dictionary with sync stats
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
