"""Tests for reMarkable Cloud API client."""

import io
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx

from remarkawise.models import DocumentType
from remarkawise.remarkable.client import (
    RemarkableAPIError,
    RemarkableAuthError,
    RemarkableClient,
)

from .conftest import MOCK_REMARKABLE_DOCS_RESPONSE


class TestRemarkableClientAuth:
    """Tests for reMarkable authentication."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_register_device_success(self) -> None:
        """Test successful device registration."""
        # Mock the registration endpoint
        respx.post(
            "https://webapp-prod.cloud.remarkable.engineering/token/json/2/device/new"
        ).respond(status_code=200, text='"test-device-token-12345"')

        client = RemarkableClient()
        token = await client.register_device("ABC123")

        assert token == "test-device-token-12345"
        assert client.device_token == "test-device-token-12345"
        client.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_register_device_invalid_code(self) -> None:
        """Test device registration with invalid code."""
        respx.post(
            "https://webapp-prod.cloud.remarkable.engineering/token/json/2/device/new"
        ).respond(status_code=400, text="Invalid code")

        client = RemarkableClient()
        with pytest.raises(RemarkableAuthError) as exc_info:
            await client.register_device("INVALID")

        assert "Failed to register device" in str(exc_info.value)
        client.close()

    @respx.mock
    def test_get_user_token_success(self) -> None:
        """Test getting user token from device token."""
        respx.post(
            "https://webapp-prod.cloud.remarkable.engineering/token/json/2/user/new"
        ).respond(status_code=200, text='"user-token-xyz"')

        client = RemarkableClient(device_token="test-device-token")
        token = client._ensure_user_token()

        assert token == "user-token-xyz"
        assert client._user_token == "user-token-xyz"
        client.close()

    @respx.mock
    def test_get_user_token_without_device_token(self) -> None:
        """Test getting user token without device token fails."""
        client = RemarkableClient()

        with pytest.raises(RemarkableAuthError) as exc_info:
            client._ensure_user_token()

        assert "No device token" in str(exc_info.value)
        client.close()

    @respx.mock
    def test_get_user_token_expired_device_token(self) -> None:
        """Test getting user token with expired device token."""
        respx.post(
            "https://webapp-prod.cloud.remarkable.engineering/token/json/2/user/new"
        ).respond(status_code=401, text="Unauthorized")

        client = RemarkableClient(device_token="expired-token")

        with pytest.raises(RemarkableAuthError) as exc_info:
            client._ensure_user_token()

        assert "Failed to get user token" in str(exc_info.value)
        client.close()


class TestRemarkableClientDocuments:
    """Tests for document operations."""

    @respx.mock
    def test_list_documents_success(self) -> None:
        """Test listing documents from cloud."""
        # Mock user token endpoint
        respx.post(
            "https://webapp-prod.cloud.remarkable.engineering/token/json/2/user/new"
        ).respond(status_code=200, text='"user-token"')

        # Mock service discovery
        respx.get(
            "https://service-manager-production-dot-remarkable-production.appspot.com/service/json/1/document-storage"
        ).respond(status_code=200, json={"Host": "storage.remarkable.com"})

        # Mock document list endpoint
        respx.get("https://storage.remarkable.com/document-storage/json/2/docs").respond(
            status_code=200, json=MOCK_REMARKABLE_DOCS_RESPONSE
        )

        client = RemarkableClient(device_token="test-token")
        documents = client.list_documents()

        # Should return 2 documents (folder excluded)
        assert len(documents) == 2

        # Check first document
        doc1 = next(d for d in documents if d.id == "doc-001")
        assert doc1.name == "Research Paper"
        assert doc1.document_type == DocumentType.PDF
        assert doc1.version == 2

        # Check second document
        doc2 = next(d for d in documents if d.id == "doc-002")
        assert doc2.name == "Meeting Notes"
        assert doc2.parent_id == "folder-001"

        client.close()

    @respx.mock
    def test_list_documents_empty(self) -> None:
        """Test listing when no documents exist."""
        respx.post(
            "https://webapp-prod.cloud.remarkable.engineering/token/json/2/user/new"
        ).respond(status_code=200, text='"user-token"')

        respx.get(
            "https://service-manager-production-dot-remarkable-production.appspot.com/service/json/1/document-storage"
        ).respond(status_code=200, json={"Host": "storage.remarkable.com"})

        respx.get("https://storage.remarkable.com/document-storage/json/2/docs").respond(
            status_code=200, json=[]
        )

        client = RemarkableClient(device_token="test-token")
        documents = client.list_documents()

        assert len(documents) == 0
        client.close()

    @respx.mock
    def test_list_documents_api_error(self) -> None:
        """Test handling API error when listing documents."""
        respx.post(
            "https://webapp-prod.cloud.remarkable.engineering/token/json/2/user/new"
        ).respond(status_code=200, text='"user-token"')

        respx.get(
            "https://service-manager-production-dot-remarkable-production.appspot.com/service/json/1/document-storage"
        ).respond(status_code=200, json={"Host": "storage.remarkable.com"})

        respx.get("https://storage.remarkable.com/document-storage/json/2/docs").respond(
            status_code=500, text="Internal Server Error"
        )

        client = RemarkableClient(device_token="test-token")

        with pytest.raises(RemarkableAPIError) as exc_info:
            client.list_documents()

        assert "Failed to list documents" in str(exc_info.value)
        client.close()

    @respx.mock
    def test_download_document_success(self, temp_dir: Path, sample_pdf: Path) -> None:
        """Test downloading a document."""
        # Create a mock ZIP archive
        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, "w") as zf:
            zf.write(sample_pdf, "document.pdf")
            zf.writestr("doc-001.content", '{"pages": []}')
        archive_data = archive_buffer.getvalue()

        # Mock endpoints
        respx.post(
            "https://webapp-prod.cloud.remarkable.engineering/token/json/2/user/new"
        ).respond(status_code=200, text='"user-token"')

        respx.get(
            "https://service-manager-production-dot-remarkable-production.appspot.com/service/json/1/document-storage"
        ).respond(status_code=200, json={"Host": "storage.remarkable.com"})

        respx.get("https://storage.remarkable.com/document-storage/json/2/docs").respond(
            status_code=200,
            json=[{"ID": "doc-001", "BlobURLGet": "https://storage.remarkable.com/blob/doc-001"}],
        )

        respx.get("https://storage.remarkable.com/blob/doc-001").respond(
            status_code=200, content=archive_data
        )

        client = RemarkableClient(device_token="test-token")
        doc_dir = client.download_document("doc-001", temp_dir)

        assert doc_dir.exists()
        assert (doc_dir / "document.pdf").exists()
        client.close()

    @respx.mock
    def test_download_document_not_found(self, temp_dir: Path) -> None:
        """Test downloading a non-existent document."""
        respx.post(
            "https://webapp-prod.cloud.remarkable.engineering/token/json/2/user/new"
        ).respond(status_code=200, text='"user-token"')

        respx.get(
            "https://service-manager-production-dot-remarkable-production.appspot.com/service/json/1/document-storage"
        ).respond(status_code=200, json={"Host": "storage.remarkable.com"})

        respx.get("https://storage.remarkable.com/document-storage/json/2/docs").respond(
            status_code=200, json=[]
        )

        client = RemarkableClient(device_token="test-token")

        with pytest.raises(RemarkableAPIError) as exc_info:
            client.download_document("nonexistent-doc", temp_dir)

        assert "Document not found" in str(exc_info.value)
        client.close()


class TestRemarkableClientContextManager:
    """Tests for context manager usage."""

    def test_context_manager(self) -> None:
        """Test using client as context manager."""
        with RemarkableClient(device_token="test-token") as client:
            assert client.device_token == "test-token"
            assert client._client is not None

    def test_close_cleans_up(self) -> None:
        """Test that close() cleans up resources."""
        client = RemarkableClient(device_token="test-token")
        http_client = client._client

        client.close()

        # Client should be closed (will raise if used)
        assert http_client.is_closed
