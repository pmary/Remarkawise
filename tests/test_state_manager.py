"""Tests for sync state management."""

from datetime import datetime
from pathlib import Path

import pytest

from remarkawise.models import Highlight, SyncState
from remarkawise.sync.state import StateManager


class TestStateManagerBasics:
    """Basic tests for StateManager."""

    def test_create_state_manager(self, temp_dir: Path) -> None:
        """Test creating a state manager."""
        db_path = temp_dir / "test.db"
        manager = StateManager(db_path)

        assert db_path.exists()
        manager.close()

    def test_creates_parent_directories(self, temp_dir: Path) -> None:
        """Test that parent directories are created."""
        db_path = temp_dir / "nested" / "path" / "test.db"
        manager = StateManager(db_path)

        assert db_path.exists()
        manager.close()


class TestSyncStateOperations:
    """Tests for sync state CRUD operations."""

    def test_get_nonexistent_state(self, state_manager: StateManager) -> None:
        """Test getting state for document that hasn't been synced."""
        state = state_manager.get_sync_state("nonexistent-doc")
        assert state is None

    def test_update_and_get_state(self, state_manager: StateManager) -> None:
        """Test updating and retrieving sync state."""
        state = SyncState(
            document_id="doc-123",
            document_name="Test Document",
            last_synced_at=datetime(2024, 1, 15, 10, 30, 0),
            last_version=3,
            highlight_count=10,
            checksum="abc123",
        )

        state_manager.update_sync_state(state)

        retrieved = state_manager.get_sync_state("doc-123")

        assert retrieved is not None
        assert retrieved.document_id == "doc-123"
        assert retrieved.document_name == "Test Document"
        assert retrieved.last_version == 3
        assert retrieved.highlight_count == 10
        assert retrieved.checksum == "abc123"

    def test_update_existing_state(self, state_manager: StateManager) -> None:
        """Test updating an existing sync state."""
        # Initial state
        state1 = SyncState(
            document_id="doc-123",
            document_name="Test Document",
            last_synced_at=datetime(2024, 1, 15),
            last_version=1,
            highlight_count=5,
        )
        state_manager.update_sync_state(state1)

        # Updated state
        state2 = SyncState(
            document_id="doc-123",
            document_name="Test Document (Updated)",
            last_synced_at=datetime(2024, 1, 16),
            last_version=2,
            highlight_count=8,
        )
        state_manager.update_sync_state(state2)

        retrieved = state_manager.get_sync_state("doc-123")

        assert retrieved is not None
        assert retrieved.document_name == "Test Document (Updated)"
        assert retrieved.last_version == 2
        assert retrieved.highlight_count == 8

    def test_needs_sync_new_document(self, state_manager: StateManager) -> None:
        """Test needs_sync for new document."""
        assert state_manager.needs_sync("new-doc", 1) is True

    def test_needs_sync_same_version(self, state_manager: StateManager) -> None:
        """Test needs_sync when version hasn't changed."""
        state = SyncState(
            document_id="doc-123",
            document_name="Test",
            last_synced_at=datetime.utcnow(),
            last_version=5,
        )
        state_manager.update_sync_state(state)

        assert state_manager.needs_sync("doc-123", 5) is False

    def test_needs_sync_newer_version(self, state_manager: StateManager) -> None:
        """Test needs_sync when document has newer version."""
        state = SyncState(
            document_id="doc-123",
            document_name="Test",
            last_synced_at=datetime.utcnow(),
            last_version=5,
        )
        state_manager.update_sync_state(state)

        assert state_manager.needs_sync("doc-123", 6) is True


class TestHighlightTracking:
    """Tests for highlight sync tracking."""

    def test_is_highlight_synced_new(self, state_manager: StateManager) -> None:
        """Test checking unsynced highlight."""
        assert state_manager.is_highlight_synced("new-highlight") is False

    def test_mark_highlights_synced(
        self, state_manager: StateManager, sample_highlights: list[Highlight]
    ) -> None:
        """Test marking highlights as synced."""
        state_manager.mark_highlights_synced("doc-123", sample_highlights)

        for hl in sample_highlights:
            assert state_manager.is_highlight_synced(hl.id) is True

    def test_get_unsynced_highlights(
        self, state_manager: StateManager, sample_highlights: list[Highlight]
    ) -> None:
        """Test filtering to unsynced highlights."""
        # Mark first highlight as synced
        state_manager.mark_highlights_synced("doc-123", [sample_highlights[0]])

        unsynced = state_manager.get_unsynced_highlights("doc-123", sample_highlights)

        assert len(unsynced) == 2
        assert sample_highlights[0] not in unsynced
        assert sample_highlights[1] in unsynced
        assert sample_highlights[2] in unsynced

    def test_get_unsynced_highlights_all_new(
        self, state_manager: StateManager, sample_highlights: list[Highlight]
    ) -> None:
        """Test when all highlights are new."""
        unsynced = state_manager.get_unsynced_highlights("doc-123", sample_highlights)

        assert len(unsynced) == 3

    def test_get_unsynced_highlights_all_synced(
        self, state_manager: StateManager, sample_highlights: list[Highlight]
    ) -> None:
        """Test when all highlights are already synced."""
        state_manager.mark_highlights_synced("doc-123", sample_highlights)

        unsynced = state_manager.get_unsynced_highlights("doc-123", sample_highlights)

        assert len(unsynced) == 0


class TestChecksumCalculation:
    """Tests for highlight checksum calculation."""

    def test_calculate_checksum(
        self, state_manager: StateManager, sample_highlights: list[Highlight]
    ) -> None:
        """Test calculating checksum for highlights."""
        checksum = state_manager.calculate_highlights_checksum(sample_highlights)

        assert len(checksum) == 32  # SHA256 truncated to 32 chars
        assert checksum.isalnum()

    def test_checksum_deterministic(
        self, state_manager: StateManager, sample_highlights: list[Highlight]
    ) -> None:
        """Test that checksum is deterministic."""
        checksum1 = state_manager.calculate_highlights_checksum(sample_highlights)
        checksum2 = state_manager.calculate_highlights_checksum(sample_highlights)

        assert checksum1 == checksum2

    def test_checksum_changes_with_content(
        self, state_manager: StateManager, sample_highlights: list[Highlight]
    ) -> None:
        """Test that checksum changes when content changes."""
        checksum1 = state_manager.calculate_highlights_checksum(sample_highlights)

        # Modify a highlight
        modified_highlights = sample_highlights.copy()
        modified_highlights[0] = Highlight(
            id=sample_highlights[0].id,
            document_id=sample_highlights[0].document_id,
            text="Different text content",
            page_number=sample_highlights[0].page_number,
        )

        checksum2 = state_manager.calculate_highlights_checksum(modified_highlights)

        assert checksum1 != checksum2

    def test_checksum_empty_list(self, state_manager: StateManager) -> None:
        """Test checksum of empty highlight list."""
        checksum = state_manager.calculate_highlights_checksum([])

        assert len(checksum) == 32


class TestSyncedDocuments:
    """Tests for getting synced documents."""

    def test_get_all_synced_documents_empty(
        self, state_manager: StateManager
    ) -> None:
        """Test getting synced documents when none exist."""
        docs = state_manager.get_all_synced_documents()
        assert docs == []

    def test_get_all_synced_documents(self, state_manager: StateManager) -> None:
        """Test getting all synced documents."""
        states = [
            SyncState(
                document_id=f"doc-{i}",
                document_name=f"Document {i}",
                last_synced_at=datetime(2024, 1, i + 1),
                last_version=1,
            )
            for i in range(3)
        ]

        for state in states:
            state_manager.update_sync_state(state)

        docs = state_manager.get_all_synced_documents()

        assert len(docs) == 3
        # Should be sorted by last_synced_at descending
        assert docs[0].document_id == "doc-2"  # Most recent

    def test_get_stats(self, state_manager: StateManager) -> None:
        """Test getting sync statistics."""
        # Add some synced documents
        for i in range(3):
            state = SyncState(
                document_id=f"doc-{i}",
                document_name=f"Document {i}",
                last_synced_at=datetime.utcnow(),
                last_version=1,
                highlight_count=5 + i,
            )
            state_manager.update_sync_state(state)

        stats = state_manager.get_stats()

        assert stats["documents_synced"] == 3
        assert stats["total_highlights"] == 18  # 5 + 6 + 7

    def test_get_stats_empty(self, state_manager: StateManager) -> None:
        """Test getting stats with no data."""
        stats = state_manager.get_stats()

        assert stats["documents_synced"] == 0
        assert stats["total_highlights"] == 0


class TestDeletionTracking:
    """Tests for highlight deletion tracking."""

    def test_mark_highlights_with_readwise_ids(
        self, state_manager: StateManager, sample_highlights: list[Highlight]
    ) -> None:
        """Test marking highlights with Readwise IDs."""
        import hashlib

        # Create readwise_ids mapping
        readwise_ids = {}
        for i, hl in enumerate(sample_highlights):
            text_hash = hashlib.sha256(hl.text.encode()).hexdigest()[:32]
            readwise_ids[text_hash] = 1000 + i

        state_manager.mark_highlights_synced("doc-123", sample_highlights, readwise_ids)

        # Verify highlights are synced
        for hl in sample_highlights:
            assert state_manager.is_highlight_synced(hl.id) is True

        # Verify Readwise IDs are stored
        synced = state_manager.get_synced_highlights_for_document("doc-123")
        assert len(synced) == 3

        for i, hl_data in enumerate(synced):
            assert hl_data["readwise_id"] is not None

    def test_get_synced_highlights_for_document(
        self, state_manager: StateManager, sample_highlights: list[Highlight]
    ) -> None:
        """Test getting synced highlights for a document."""
        state_manager.mark_highlights_synced("doc-123", sample_highlights)

        synced = state_manager.get_synced_highlights_for_document("doc-123")

        assert len(synced) == 3
        highlight_ids = {s["highlight_id"] for s in synced}
        for hl in sample_highlights:
            assert hl.id in highlight_ids

    def test_get_synced_highlights_empty(self, state_manager: StateManager) -> None:
        """Test getting synced highlights when none exist."""
        synced = state_manager.get_synced_highlights_for_document("nonexistent")
        assert synced == []

    def test_get_deleted_highlights(
        self, state_manager: StateManager, sample_highlights: list[Highlight]
    ) -> None:
        """Test detecting deleted highlights."""
        # Sync all highlights
        state_manager.mark_highlights_synced("doc-123", sample_highlights)

        # Current highlights (missing the first one)
        current = sample_highlights[1:]

        deleted = state_manager.get_deleted_highlights("doc-123", current)

        assert len(deleted) == 1
        assert deleted[0]["highlight_id"] == sample_highlights[0].id

    def test_get_deleted_highlights_none(
        self, state_manager: StateManager, sample_highlights: list[Highlight]
    ) -> None:
        """Test when no highlights have been deleted."""
        state_manager.mark_highlights_synced("doc-123", sample_highlights)

        deleted = state_manager.get_deleted_highlights("doc-123", sample_highlights)

        assert len(deleted) == 0

    def test_remove_highlights(
        self, state_manager: StateManager, sample_highlights: list[Highlight]
    ) -> None:
        """Test removing highlights from tracking."""
        state_manager.mark_highlights_synced("doc-123", sample_highlights)

        # Remove first highlight
        state_manager.remove_highlights([sample_highlights[0].id])

        # Verify it's removed
        assert state_manager.is_highlight_synced(sample_highlights[0].id) is False
        assert state_manager.is_highlight_synced(sample_highlights[1].id) is True

    def test_remove_highlights_empty_list(self, state_manager: StateManager) -> None:
        """Test removing with empty list does nothing."""
        state_manager.remove_highlights([])  # Should not raise
