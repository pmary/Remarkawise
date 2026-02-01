"""reMarkable Cloud API client.

The reMarkable Cloud API uses a two-phase authentication:
1. One-time code from https://my.remarkable.com/device/desktop/connect
2. Exchange code for device token (persistent)

Documents are stored as ZIP archives containing:
- PDF/EPUB content
- .rm files with annotations/highlights
- .metadata files with document info
"""

import io
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

import httpx

from remarkawise.models import DocumentType, RemarkableDocument


class RemarkableAuthError(Exception):
    """Authentication error with reMarkable Cloud."""

    pass


class RemarkableAPIError(Exception):
    """General API error from reMarkable Cloud."""

    pass


class RemarkableClient:
    """Client for interacting with reMarkable Cloud API."""

    # API endpoints
    AUTH_HOST = "https://webapp-prod.cloud.remarkable.engineering"
    SYNC_HOST = "https://internal.cloud.remarkable.com"
    STORAGE_HOST = "https://storage.cloud.remarkable.com"

    # Discovery endpoint for getting actual service URLs
    DISCOVERY_URL = "https://service-manager-production-dot-remarkable-production.appspot.com"

    def __init__(self, device_token: Optional[str] = None) -> None:
        """Initialize the client.

        Args:
            device_token: Persistent device token for authentication.
                         If not provided, must call register_device first.
        """
        self.device_token = device_token
        self._user_token: Optional[str] = None
        self._sync_host: Optional[str] = None
        self._storage_host: Optional[str] = None
        self._client = httpx.Client(timeout=30.0)

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self) -> "RemarkableClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    async def register_device(self, one_time_code: str) -> str:
        """Register a new device and get a device token.

        Args:
            one_time_code: One-time code from remarkable.com/device/desktop/connect

        Returns:
            Device token to save for future authentication

        Raises:
            RemarkableAuthError: If registration fails
        """
        device_id = str(uuid4())
        device_desc = "desktop-windows"  # Identifies as desktop client

        payload = {
            "code": one_time_code,
            "deviceDesc": device_desc,
            "deviceID": device_id,
        }

        response = self._client.post(
            f"{self.AUTH_HOST}/token/json/2/device/new",
            json=payload,
        )

        if response.status_code != 200:
            raise RemarkableAuthError(
                f"Failed to register device: {response.status_code} - {response.text}"
            )

        self.device_token = response.text.strip('"')
        return self.device_token

    def _ensure_user_token(self) -> str:
        """Get or refresh the user token.

        The user token is short-lived and derived from the device token.
        """
        if not self.device_token:
            raise RemarkableAuthError("No device token. Call register_device first.")

        response = self._client.post(
            f"{self.AUTH_HOST}/token/json/2/user/new",
            headers={"Authorization": f"Bearer {self.device_token}"},
        )

        if response.status_code != 200:
            raise RemarkableAuthError(
                f"Failed to get user token: {response.status_code} - {response.text}"
            )

        self._user_token = response.text.strip('"')
        return self._user_token

    def _get_auth_headers(self) -> dict[str, str]:
        """Get headers with current authentication."""
        if not self._user_token:
            self._ensure_user_token()
        return {"Authorization": f"Bearer {self._user_token}"}

    def _discover_storage(self) -> str:
        """Discover the storage service URL."""
        if self._storage_host:
            return self._storage_host

        response = self._client.get(
            f"{self.DISCOVERY_URL}/service/json/1/document-storage",
            params={"environment": "production", "group": "auth0|storage", "apiVer": "2"},
            headers=self._get_auth_headers(),
        )

        if response.status_code == 200:
            data = response.json()
            self._storage_host = f"https://{data.get('Host', 'storage.cloud.remarkable.com')}"
        else:
            self._storage_host = self.STORAGE_HOST

        return self._storage_host

    def list_documents(self) -> list[RemarkableDocument]:
        """List all documents in the cloud.

        Returns:
            List of documents with metadata
        """
        storage_host = self._discover_storage()

        response = self._client.get(
            f"{storage_host}/document-storage/json/2/docs",
            headers=self._get_auth_headers(),
            params={"withBlob": "true"},
        )

        if response.status_code != 200:
            raise RemarkableAPIError(
                f"Failed to list documents: {response.status_code} - {response.text}"
            )

        documents = []
        for item in response.json():
            # Skip folders (Type == "CollectionType")
            if item.get("Type") == "CollectionType":
                continue

            # Determine document type
            doc_type = DocumentType.PDF
            if item.get("fileType") == "epub":
                doc_type = DocumentType.EPUB
            elif item.get("fileType") == "notebook":
                doc_type = DocumentType.NOTEBOOK

            # Parse modification time
            modified_str = item.get("ModifiedClient", item.get("Modified", ""))
            try:
                modified_time = datetime.fromisoformat(modified_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                modified_time = datetime.utcnow()

            doc = RemarkableDocument(
                id=item.get("ID", ""),
                name=item.get("VissibleName", item.get("VisibleName", "Untitled")),
                document_type=doc_type,
                parent_id=item.get("Parent"),
                modified_time=modified_time,
                version=item.get("Version", 1),
            )
            documents.append(doc)

        return documents

    def download_document(self, doc_id: str, output_dir: Path) -> Path:
        """Download a document archive from the cloud.

        Args:
            doc_id: Document ID to download
            output_dir: Directory to save the extracted files

        Returns:
            Path to the extracted document directory
        """
        storage_host = self._discover_storage()

        # Get download URL
        response = self._client.get(
            f"{storage_host}/document-storage/json/2/docs",
            headers=self._get_auth_headers(),
            params={"doc": doc_id, "withBlob": "true"},
        )

        if response.status_code != 200:
            raise RemarkableAPIError(
                f"Failed to get document info: {response.status_code}"
            )

        docs = response.json()
        if not docs:
            raise RemarkableAPIError(f"Document not found: {doc_id}")

        blob_url = docs[0].get("BlobURLGet")
        if not blob_url:
            raise RemarkableAPIError(f"No download URL for document: {doc_id}")

        # Download the blob (ZIP archive)
        response = self._client.get(blob_url)
        if response.status_code != 200:
            raise RemarkableAPIError(
                f"Failed to download document: {response.status_code}"
            )

        # Extract the archive
        doc_dir = output_dir / doc_id
        doc_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            zf.extractall(doc_dir)

        return doc_dir

    def get_document_with_highlights(
        self, doc: RemarkableDocument, cache_dir: Path
    ) -> tuple[Optional[Path], list[Path]]:
        """Download document and return paths to PDF and highlight files.

        Args:
            doc: Document metadata
            cache_dir: Directory for caching downloads

        Returns:
            Tuple of (pdf_path, list of .rm highlight file paths)
        """
        doc_dir = self.download_document(doc.id, cache_dir)

        # Find the PDF file
        pdf_path: Optional[Path] = None
        pdf_files = list(doc_dir.glob("*.pdf"))
        if pdf_files:
            pdf_path = pdf_files[0]

        # Find .rm highlight files (stored in <doc_id>/ subdirectory)
        rm_files = list(doc_dir.rglob("*.rm"))

        return pdf_path, rm_files
