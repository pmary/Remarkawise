"""Tests for sync engine."""

import io
import json
import struct
import zipfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import respx

from remarkawise.config import Settings
from remarkawise.models import DocumentType, Highlight, RemarkableDocument, SyncResult
from remarkawise.readwise.client import ReadwiseClient, ReadwiseRateLimitError
from remarkawise.remarkable.local_cache import LocalCacheClient
from remarkawise.sync.engine import SyncEngine
from remarkawise.sync.state import StateManager


class TestSyncEngineInit:
    """Tests for SyncEngine initialization."""

    def test_init_with_settings(self, settings: Settings) -> None:
        """Test initializing sync engine with settings."""
        engine = SyncEngine(settings)

        assert engine.settings == settings
        engine.close()

    def test_init_creates_directories(self, settings: Settings) -> None:
        """Test that init creates necessary directories."""
        engine = SyncEngine(settings)

        assert settings.data_dir.exists()
        assert settings.cache_dir.exists()
        engine.close()

    def test_readwise_client_requires_token(self, temp_dir: Path) -> None:
        """Test that Readwise client requires access token."""
        settings = Settings(
            readwise_access_token=None,
            data_dir=temp_dir,
        )
        engine = SyncEngine(settings)

        with pytest.raises(ValueError) as exc_info:
            _ = engine.readwise_client

        assert "access token not configured" in str(exc_info.value)
        engine.close()


class TestSyncEngineSync:
    """Tests for sync operation."""

    @pytest.fixture
    def mock_remarkable_client(self) -> MagicMock:
        """Create a mock reMarkable local cache client."""
        client = MagicMock(spec=LocalCacheClient)
        client.list_documents.return_value = [
            RemarkableDocument(
                id="doc-001",
                name="Test Document",
                document_type=DocumentType.PDF,
                modified_time=datetime(2024, 1, 15),
                version=1,
            )
        ]
        return client

    @pytest.fixture
    def mock_readwise_client(self) -> MagicMock:
        """Create a mock Readwise client."""
        client = MagicMock(spec=ReadwiseClient)
        client.create_highlights.return_value = {"created": 3}
        return client

    def test_sync_no_documents(
        self,
        settings: Settings,
        mock_remarkable_client: MagicMock,
        mock_readwise_client: MagicMock,
    ) -> None:
        """Test sync when no documents exist."""
        mock_remarkable_client.list_documents.return_value = []

        engine = SyncEngine(
            settings,
            remarkable_client=mock_remarkable_client,
            readwise_client=mock_readwise_client,
        )

        result = engine.sync()

        assert result.success is True
        assert result.documents_processed == 0
        assert result.highlights_synced == 0
        engine.close()

    def test_sync_filters_unsupported_types(
        self,
        settings: Settings,
        mock_remarkable_client: MagicMock,
        mock_readwise_client: MagicMock,
    ) -> None:
        """Test that sync filters out unsupported document types (notebooks)."""
        mock_remarkable_client.list_documents.return_value = [
            RemarkableDocument(
                id="doc-001",
                name="PDF Document",
                document_type=DocumentType.PDF,
                modified_time=datetime.utcnow(),
                version=1,
            ),
            RemarkableDocument(
                id="doc-002",
                name="Notebook",
                document_type=DocumentType.NOTEBOOK,
                modified_time=datetime.utcnow(),
                version=1,
            ),
            RemarkableDocument(
                id="doc-003",
                name="EPUB Book",
                document_type=DocumentType.EPUB,
                modified_time=datetime.utcnow(),
                version=1,
            ),
        ]

        # Mock to return no highlights for simplicity
        mock_remarkable_client.get_document_with_highlights.return_value = (None, [])

        engine = SyncEngine(
            settings,
            remarkable_client=mock_remarkable_client,
            readwise_client=mock_readwise_client,
        )

        result = engine.sync()

        # PDF and EPUB should be processed, NOTEBOOK should be filtered out
        assert mock_remarkable_client.get_document_with_highlights.call_count == 2
        engine.close()

    def test_sync_processes_synced_documents(
        self,
        settings: Settings,
        mock_remarkable_client: MagicMock,
        mock_readwise_client: MagicMock,
        state_manager: StateManager,
    ) -> None:
        """Test that sync processes documents even if previously synced.

        The sync engine always checks documents for new highlights since
        document version doesn't change when highlights are added.
        """
        from remarkawise.models import SyncState

        # Pre-mark document as synced
        state = SyncState(
            document_id="doc-001",
            document_name="Test Document",
            last_synced_at=datetime.utcnow(),
            last_version=1,
        )
        state_manager.update_sync_state(state)

        # Return no PDF so processing ends early
        mock_remarkable_client.get_document_with_highlights.return_value = (None, [])

        engine = SyncEngine(
            settings,
            remarkable_client=mock_remarkable_client,
            readwise_client=mock_readwise_client,
            state_manager=state_manager,
        )

        result = engine.sync()

        # Document should still be processed (but no highlights synced)
        mock_remarkable_client.get_document_with_highlights.assert_called()
        assert result.highlights_synced == 0
        engine.close()

    def test_sync_force_resyncs(
        self,
        settings: Settings,
        mock_remarkable_client: MagicMock,
        mock_readwise_client: MagicMock,
        state_manager: StateManager,
    ) -> None:
        """Test that force=True re-syncs already synced documents."""
        from remarkawise.models import SyncState

        # Pre-mark document as synced
        state = SyncState(
            document_id="doc-001",
            document_name="Test Document",
            last_synced_at=datetime.utcnow(),
            last_version=1,
        )
        state_manager.update_sync_state(state)

        mock_remarkable_client.get_document_with_highlights.return_value = (None, [])

        engine = SyncEngine(
            settings,
            remarkable_client=mock_remarkable_client,
            readwise_client=mock_readwise_client,
            state_manager=state_manager,
        )

        result = engine.sync(force=True)

        # Should download document even though it's synced
        mock_remarkable_client.get_document_with_highlights.assert_called()
        engine.close()

    def test_sync_specific_document(
        self,
        settings: Settings,
        mock_remarkable_client: MagicMock,
        mock_readwise_client: MagicMock,
    ) -> None:
        """Test syncing a specific document by ID."""
        mock_remarkable_client.list_documents.return_value = [
            RemarkableDocument(
                id="doc-001",
                name="Document 1",
                document_type=DocumentType.PDF,
                modified_time=datetime.utcnow(),
                version=1,
            ),
            RemarkableDocument(
                id="doc-002",
                name="Document 2",
                document_type=DocumentType.PDF,
                modified_time=datetime.utcnow(),
                version=1,
            ),
        ]

        mock_remarkable_client.get_document_with_highlights.return_value = (None, [])

        engine = SyncEngine(
            settings,
            remarkable_client=mock_remarkable_client,
            readwise_client=mock_readwise_client,
        )

        result = engine.sync(document_ids=["doc-002"])

        # Should only process doc-002
        calls = mock_remarkable_client.get_document_with_highlights.call_args_list
        assert len(calls) == 1
        assert calls[0][0][0].id == "doc-002"
        engine.close()

    def test_sync_handles_document_error(
        self,
        settings: Settings,
        mock_remarkable_client: MagicMock,
        mock_readwise_client: MagicMock,
    ) -> None:
        """Test that sync continues after document error."""
        mock_remarkable_client.list_documents.return_value = [
            RemarkableDocument(
                id="doc-001",
                name="Bad Document",
                document_type=DocumentType.PDF,
                modified_time=datetime.utcnow(),
                version=1,
            ),
            RemarkableDocument(
                id="doc-002",
                name="Good Document",
                document_type=DocumentType.PDF,
                modified_time=datetime.utcnow(),
                version=1,
            ),
        ]

        # First call raises error, second succeeds
        # Using OSError which is caught by the sync engine
        mock_remarkable_client.get_document_with_highlights.side_effect = [
            OSError("Download failed"),
            (None, []),  # No highlights
        ]

        engine = SyncEngine(
            settings,
            remarkable_client=mock_remarkable_client,
            readwise_client=mock_readwise_client,
        )

        result = engine.sync()

        # Should have processed second document
        assert result.documents_processed == 1
        assert len(result.errors) == 1
        assert "Bad Document" in result.errors[0]
        engine.close()


class TestSyncEngineStatus:
    """Tests for sync status."""

    def test_get_status_empty(self, settings: Settings) -> None:
        """Test getting status with no synced documents."""
        engine = SyncEngine(settings)

        status = engine.get_status()

        assert status["stats"]["documents_synced"] == 0
        assert status["stats"]["total_highlights"] == 0
        assert status["recent_syncs"] == []
        engine.close()

    def test_get_status_with_synced_documents(
        self, settings: Settings, state_manager: StateManager
    ) -> None:
        """Test getting status with synced documents."""
        from remarkawise.models import SyncState

        # Add some synced documents
        for i in range(3):
            state = SyncState(
                document_id=f"doc-{i}",
                document_name=f"Document {i}",
                last_synced_at=datetime(2024, 1, i + 1),
                last_version=1,
                highlight_count=5 * (i + 1),
            )
            state_manager.update_sync_state(state)

        engine = SyncEngine(settings, state_manager=state_manager)

        status = engine.get_status()

        assert status["stats"]["documents_synced"] == 3
        assert status["stats"]["total_highlights"] == 30  # 5 + 10 + 15
        assert len(status["recent_syncs"]) == 3
        engine.close()


class TestSyncEngineRateLimit:
    """Tests for rate limit handling."""

    def test_upload_with_retry_succeeds_after_rate_limit(
        self, settings: Settings
    ) -> None:
        """Test that upload retries after rate limit."""
        mock_client = MagicMock(spec=ReadwiseClient)

        # First call raises rate limit, second succeeds
        mock_client.create_highlights.side_effect = [
            ReadwiseRateLimitError(retry_after=0),  # 0 seconds for fast test
            {"created": 3},
        ]

        engine = SyncEngine(settings, readwise_client=mock_client)

        # Should not raise
        engine._upload_with_retry([MagicMock()] * 3)

        assert mock_client.create_highlights.call_count == 2
        engine.close()

    def test_upload_with_retry_raises_after_max_retries(
        self, settings: Settings
    ) -> None:
        """Test that upload raises after max retries exceeded."""
        mock_client = MagicMock(spec=ReadwiseClient)

        # Always raise rate limit
        mock_client.create_highlights.side_effect = ReadwiseRateLimitError(retry_after=0)

        engine = SyncEngine(settings, readwise_client=mock_client)

        with pytest.raises(ReadwiseRateLimitError):
            engine._upload_with_retry([MagicMock()] * 3, max_retries=3)

        assert mock_client.create_highlights.call_count == 3
        engine.close()


class TestSyncEngineCleanup:
    """Tests for cleanup and resource management."""

    def test_close_cleans_up_all(self, settings: Settings) -> None:
        """Test that close() cleans up all resources."""
        mock_rm = MagicMock(spec=LocalCacheClient)
        mock_rw = MagicMock(spec=ReadwiseClient)

        engine = SyncEngine(
            settings,
            remarkable_client=mock_rm,
            readwise_client=mock_rw,
        )

        engine.close()

        mock_rm.close.assert_called_once()
        mock_rw.close.assert_called_once()
