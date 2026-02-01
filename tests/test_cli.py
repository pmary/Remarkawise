"""Tests for CLI commands."""

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from remarkawise.cli import app
from remarkawise.models import DocumentType, RemarkableDocument, SyncResult


runner = CliRunner()


class TestCLIVersion:
    """Tests for version command."""

    def test_version_flag(self) -> None:
        """Test --version flag."""
        result = runner.invoke(app, ["--version"])

        assert result.exit_code == 0
        assert "Remarkawise v" in result.output

    def test_version_short_flag(self) -> None:
        """Test -v flag."""
        result = runner.invoke(app, ["-v"])

        assert result.exit_code == 0
        assert "Remarkawise v" in result.output


class TestCLISync:
    """Tests for sync command."""

    @patch("remarkawise.cli.settings")
    def test_sync_no_remarkable_token(self, mock_settings: MagicMock) -> None:
        """Test sync fails without reMarkable token."""
        mock_settings.remarkable_device_token = None

        result = runner.invoke(app, ["sync"])

        assert result.exit_code == 1
        assert "reMarkable not configured" in result.output

    @patch("remarkawise.cli.settings")
    def test_sync_no_readwise_token(self, mock_settings: MagicMock) -> None:
        """Test sync fails without Readwise token."""
        mock_settings.remarkable_device_token = "rm-token"
        mock_settings.readwise_access_token = None

        result = runner.invoke(app, ["sync"])

        assert result.exit_code == 1
        assert "Readwise not configured" in result.output

    @patch("remarkawise.cli.SyncEngine")
    @patch("remarkawise.cli.settings")
    def test_sync_success(
        self, mock_settings: MagicMock, mock_engine_class: MagicMock
    ) -> None:
        """Test successful sync."""
        mock_settings.remarkable_device_token = "rm-token"
        mock_settings.readwise_access_token = "rw-token"

        mock_engine = MagicMock()
        mock_engine.sync.return_value = SyncResult(
            success=True,
            documents_processed=3,
            highlights_synced=15,
        )
        mock_engine_class.return_value = mock_engine

        result = runner.invoke(app, ["sync"])

        assert result.exit_code == 0
        assert "Sync completed successfully" in result.output
        assert "Documents processed: 3" in result.output
        assert "Highlights synced: 15" in result.output
        mock_engine.close.assert_called_once()

    @patch("remarkawise.cli.SyncEngine")
    @patch("remarkawise.cli.settings")
    def test_sync_with_errors(
        self, mock_settings: MagicMock, mock_engine_class: MagicMock
    ) -> None:
        """Test sync with some errors."""
        mock_settings.remarkable_device_token = "rm-token"
        mock_settings.readwise_access_token = "rw-token"

        mock_engine = MagicMock()
        mock_engine.sync.return_value = SyncResult(
            success=False,
            documents_processed=2,
            highlights_synced=10,
            errors=["Failed to sync document X"],
        )
        mock_engine_class.return_value = mock_engine

        result = runner.invoke(app, ["sync"])

        assert result.exit_code == 0  # Still exits 0, but shows errors
        assert "completed with errors" in result.output
        assert "Failed to sync document X" in result.output

    @patch("remarkawise.cli.SyncEngine")
    @patch("remarkawise.cli.settings")
    def test_sync_force_flag(
        self, mock_settings: MagicMock, mock_engine_class: MagicMock
    ) -> None:
        """Test sync with --force flag."""
        mock_settings.remarkable_device_token = "rm-token"
        mock_settings.readwise_access_token = "rw-token"

        mock_engine = MagicMock()
        mock_engine.sync.return_value = SyncResult(success=True)
        mock_engine_class.return_value = mock_engine

        result = runner.invoke(app, ["sync", "--force"])

        mock_engine.sync.assert_called_with(force=True, document_ids=None)

    @patch("remarkawise.cli.SyncEngine")
    @patch("remarkawise.cli.settings")
    def test_sync_document_flag(
        self, mock_settings: MagicMock, mock_engine_class: MagicMock
    ) -> None:
        """Test sync with --document flag."""
        mock_settings.remarkable_device_token = "rm-token"
        mock_settings.readwise_access_token = "rw-token"

        mock_engine = MagicMock()
        mock_engine.sync.return_value = SyncResult(success=True)
        mock_engine_class.return_value = mock_engine

        result = runner.invoke(app, ["sync", "--document", "doc-123"])

        mock_engine.sync.assert_called_with(force=False, document_ids=["doc-123"])


class TestCLIStatus:
    """Tests for status command."""

    @patch("remarkawise.cli.SyncEngine")
    @patch("remarkawise.cli.settings")
    def test_status_empty(
        self, mock_settings: MagicMock, mock_engine_class: MagicMock
    ) -> None:
        """Test status with no synced documents."""
        mock_engine = MagicMock()
        mock_engine.get_status.return_value = {
            "stats": {"documents_synced": 0, "total_highlights": 0},
            "recent_syncs": [],
        }
        mock_engine_class.return_value = mock_engine

        result = runner.invoke(app, ["status"])

        assert result.exit_code == 0
        assert "Documents synced: 0" in result.output
        assert "No documents synced yet" in result.output

    @patch("remarkawise.cli.SyncEngine")
    @patch("remarkawise.cli.settings")
    def test_status_with_documents(
        self, mock_settings: MagicMock, mock_engine_class: MagicMock
    ) -> None:
        """Test status with synced documents."""
        mock_engine = MagicMock()
        mock_engine.get_status.return_value = {
            "stats": {"documents_synced": 3, "total_highlights": 25},
            "recent_syncs": [
                {
                    "name": "Research Paper",
                    "synced_at": "2024-01-15T10:30:00",
                    "highlights": 10,
                },
                {
                    "name": "Meeting Notes",
                    "synced_at": "2024-01-14T15:00:00",
                    "highlights": 15,
                },
            ],
        }
        mock_engine_class.return_value = mock_engine

        result = runner.invoke(app, ["status"])

        assert result.exit_code == 0
        assert "Documents synced: 3" in result.output
        assert "Total highlights: 25" in result.output
        assert "Research Paper" in result.output
        assert "Meeting Notes" in result.output


class TestCLIAuth:
    """Tests for auth command."""

    def test_auth_invalid_service(self) -> None:
        """Test auth with invalid service name."""
        result = runner.invoke(app, ["auth", "invalid"])

        assert result.exit_code == 1
        assert "Unknown service" in result.output

    @patch("remarkawise.cli.settings")
    def test_auth_readwise_no_token(self, mock_settings: MagicMock) -> None:
        """Test auth readwise without token configured."""
        mock_settings.readwise_access_token = None

        result = runner.invoke(app, ["auth", "readwise"])

        assert result.exit_code == 1
        assert "No Readwise token found" in result.output
        assert "readwise.io/access_token" in result.output

    @patch("remarkawise.cli.ReadwiseClient")
    @patch("remarkawise.cli.settings")
    def test_auth_readwise_valid_token(
        self, mock_settings: MagicMock, mock_client_class: MagicMock
    ) -> None:
        """Test auth readwise with valid token."""
        mock_settings.readwise_access_token = "valid-token"

        mock_client = MagicMock()
        mock_client.verify_token.return_value = True
        mock_client_class.return_value = mock_client

        result = runner.invoke(app, ["auth", "readwise"])

        assert result.exit_code == 0
        assert "token is valid" in result.output

    @patch("remarkawise.cli.ReadwiseClient")
    @patch("remarkawise.cli.settings")
    def test_auth_readwise_invalid_token(
        self, mock_settings: MagicMock, mock_client_class: MagicMock
    ) -> None:
        """Test auth readwise with invalid token."""
        mock_settings.readwise_access_token = "invalid-token"

        mock_client = MagicMock()
        mock_client.verify_token.return_value = False
        mock_client_class.return_value = mock_client

        result = runner.invoke(app, ["auth", "readwise"])

        assert result.exit_code == 1
        assert "token is invalid" in result.output


class TestCLIListDocuments:
    """Tests for list-documents command."""

    @patch("remarkawise.cli.settings")
    def test_list_documents_no_token(self, mock_settings: MagicMock) -> None:
        """Test list-documents without reMarkable token."""
        mock_settings.remarkable_device_token = None

        result = runner.invoke(app, ["list-documents"])

        assert result.exit_code == 1
        assert "reMarkable not configured" in result.output

    @patch("remarkawise.cli.RemarkableClient")
    @patch("remarkawise.cli.settings")
    def test_list_documents_empty(
        self, mock_settings: MagicMock, mock_client_class: MagicMock
    ) -> None:
        """Test list-documents with no documents."""
        mock_settings.remarkable_device_token = "rm-token"

        mock_client = MagicMock()
        mock_client.list_documents.return_value = []
        mock_client_class.return_value = mock_client

        result = runner.invoke(app, ["list-documents"])

        assert result.exit_code == 0
        assert "No documents found" in result.output

    @patch("remarkawise.cli.RemarkableClient")
    @patch("remarkawise.cli.settings")
    def test_list_documents_with_documents(
        self, mock_settings: MagicMock, mock_client_class: MagicMock
    ) -> None:
        """Test list-documents with documents."""
        mock_settings.remarkable_device_token = "rm-token"

        mock_client = MagicMock()
        mock_client.list_documents.return_value = [
            RemarkableDocument(
                id="doc-001",
                name="Research Paper",
                document_type=DocumentType.PDF,
                modified_time=datetime(2024, 1, 15, 10, 30),
                version=1,
            ),
            RemarkableDocument(
                id="doc-002",
                name="Meeting Notes",
                document_type=DocumentType.PDF,
                modified_time=datetime(2024, 1, 14, 15, 0),
                version=1,
            ),
            RemarkableDocument(
                id="doc-003",
                name="Notebook",
                document_type=DocumentType.NOTEBOOK,  # Should be filtered
                modified_time=datetime(2024, 1, 13),
                version=1,
            ),
        ]
        mock_client_class.return_value = mock_client

        result = runner.invoke(app, ["list-documents"])

        assert result.exit_code == 0
        assert "Found 2 PDF documents" in result.output
        assert "Research Paper" in result.output
        assert "Meeting Notes" in result.output
        assert "Notebook" not in result.output  # Filtered out

    @patch("remarkawise.cli.RemarkableClient")
    @patch("remarkawise.cli.settings")
    def test_list_documents_error(
        self, mock_settings: MagicMock, mock_client_class: MagicMock
    ) -> None:
        """Test list-documents with API error."""
        mock_settings.remarkable_device_token = "rm-token"

        mock_client = MagicMock()
        mock_client.list_documents.side_effect = Exception("API Error")
        mock_client_class.return_value = mock_client

        result = runner.invoke(app, ["list-documents"])

        assert result.exit_code == 1
        assert "Error" in result.output
