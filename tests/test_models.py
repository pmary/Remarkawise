"""Tests for data models."""

from datetime import datetime

import pytest

from remarkawise.models import (
    DocumentType,
    Highlight,
    ReadwiseHighlight,
    RemarkableDocument,
    SyncResult,
    SyncState,
)


class TestRemarkableDocument:
    """Tests for RemarkableDocument model."""

    def test_create_document(self) -> None:
        """Test creating a document."""
        doc = RemarkableDocument(
            id="test-id-123",
            name="Test Document",
            document_type=DocumentType.PDF,
            modified_time=datetime.utcnow(),
        )

        assert doc.id == "test-id-123"
        assert doc.name == "Test Document"
        assert doc.document_type == DocumentType.PDF
        assert doc.parent_id is None
        assert doc.has_highlights is False

    def test_document_with_parent(self) -> None:
        """Test document with parent folder."""
        doc = RemarkableDocument(
            id="child-id",
            name="Child Doc",
            document_type=DocumentType.PDF,
            parent_id="parent-folder-id",
            modified_time=datetime.utcnow(),
        )

        assert doc.parent_id == "parent-folder-id"


class TestHighlight:
    """Tests for Highlight model."""

    def test_create_highlight(self) -> None:
        """Test creating a highlight."""
        highlight = Highlight(
            id="hl-123",
            document_id="doc-456",
            text="This is highlighted text",
            page_number=5,
        )

        assert highlight.id == "hl-123"
        assert highlight.document_id == "doc-456"
        assert highlight.text == "This is highlighted text"
        assert highlight.page_number == 5
        assert highlight.position is None
        assert highlight.note is None

    def test_highlight_with_note(self) -> None:
        """Test highlight with annotation note."""
        highlight = Highlight(
            id="hl-789",
            document_id="doc-456",
            text="Important concept",
            page_number=10,
            note="Remember this for later",
        )

        assert highlight.note == "Remember this for later"


class TestReadwiseHighlight:
    """Tests for ReadwiseHighlight model."""

    def test_create_readwise_highlight(self) -> None:
        """Test creating a Readwise-formatted highlight."""
        rw_highlight = ReadwiseHighlight(
            text="Key insight from the book",
            title="Test Book Title",
            author="Test Author",
        )

        assert rw_highlight.text == "Key insight from the book"
        assert rw_highlight.title == "Test Book Title"
        assert rw_highlight.author == "Test Author"
        assert rw_highlight.source_type == "remarkawise"
        assert rw_highlight.category == "books"


class TestSyncState:
    """Tests for SyncState model."""

    def test_create_sync_state(self) -> None:
        """Test creating sync state."""
        state = SyncState(
            document_id="doc-123",
            document_name="My Document",
            last_synced_at=datetime.utcnow(),
            last_version=3,
            highlight_count=15,
        )

        assert state.document_id == "doc-123"
        assert state.last_version == 3
        assert state.highlight_count == 15


class TestSyncResult:
    """Tests for SyncResult model."""

    def test_successful_sync(self) -> None:
        """Test successful sync result."""
        result = SyncResult(
            success=True,
            documents_processed=5,
            highlights_synced=42,
        )

        assert result.success is True
        assert result.documents_processed == 5
        assert result.highlights_synced == 42
        assert len(result.errors) == 0

    def test_sync_with_errors(self) -> None:
        """Test sync result with errors."""
        result = SyncResult(
            success=False,
            documents_processed=3,
            highlights_synced=20,
            errors=["Failed to process doc 1", "Network error on doc 2"],
        )

        assert result.success is False
        assert len(result.errors) == 2
