"""Local cache client for reading from reMarkable desktop app cache.

This client reads documents directly from the reMarkable desktop app's
local cache, eliminating the need for cloud API calls.

Cache location on macOS:
~/Library/Containers/com.remarkable.desktop/Data/Library/Application Support/remarkable/desktop/
"""

import json
import platform
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from remarkawise.models import DocumentType, RemarkableDocument


class LocalCacheError(Exception):
    """Error reading from local cache."""

    pass


class LocalCacheClient:
    """Client for reading documents from the reMarkable desktop app local cache."""

    def __init__(self, cache_path: Optional[Path] = None) -> None:
        """Initialize the local cache client.

        Args:
            cache_path: Path to the cache directory. If not provided,
                       uses the default location for the current platform.
        """
        self.cache_path = cache_path or self._get_default_cache_path()

        if not self.cache_path.exists():
            raise LocalCacheError(
                f"reMarkable desktop cache not found at: {self.cache_path}\n"
                "Make sure the reMarkable desktop app is installed and has synced."
            )

    def _get_default_cache_path(self) -> Path:
        """Get the default cache path for the current platform."""
        system = platform.system()

        if system == "Darwin":  # macOS
            return (
                Path.home()
                / "Library"
                / "Containers"
                / "com.remarkable.desktop"
                / "Data"
                / "Library"
                / "Application Support"
                / "remarkable"
                / "desktop"
            )
        elif system == "Windows":
            # Windows typically uses AppData
            return (
                Path.home()
                / "AppData"
                / "Local"
                / "remarkable"
                / "remarkable"
                / "desktop"
            )
        elif system == "Linux":
            # Linux typically uses .local/share
            return Path.home() / ".local" / "share" / "remarkable" / "desktop"
        else:
            raise LocalCacheError(f"Unsupported platform: {system}")

    def close(self) -> None:
        """Close the client (no-op for local cache)."""
        pass

    def __enter__(self) -> "LocalCacheClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def list_documents(self) -> list[RemarkableDocument]:
        """List all documents in the local cache.

        Returns:
            List of documents with metadata
        """
        documents = []

        # Find all .metadata files
        for metadata_file in self.cache_path.glob("*.metadata"):
            try:
                doc = self._parse_metadata_file(metadata_file)
                if doc:
                    documents.append(doc)
            except Exception:
                # Skip problematic files
                continue

        return documents

    def _parse_metadata_file(self, metadata_file: Path) -> Optional[RemarkableDocument]:
        """Parse a .metadata file into a RemarkableDocument.

        Args:
            metadata_file: Path to the .metadata file

        Returns:
            RemarkableDocument or None if not a valid document
        """
        with open(metadata_file) as f:
            metadata = json.load(f)

        # Skip folders (CollectionType)
        if metadata.get("type") == "CollectionType":
            return None

        # Only include DocumentType entries
        if metadata.get("type") != "DocumentType":
            return None

        # Skip deleted documents and documents in trash
        if metadata.get("deleted", False):
            return None
        if metadata.get("parent") == "trash":
            return None

        doc_id = metadata_file.stem

        # Determine document type from file existence
        doc_type = DocumentType.PDF
        if (self.cache_path / f"{doc_id}.epub").exists():
            doc_type = DocumentType.EPUB
        elif not (self.cache_path / f"{doc_id}.pdf").exists():
            # Check if it's a notebook (no PDF or EPUB)
            if (self.cache_path / doc_id).is_dir():
                doc_type = DocumentType.NOTEBOOK

        # Parse modification time
        last_modified = metadata.get("lastModified", "0")
        try:
            # reMarkable uses milliseconds since epoch
            modified_time = datetime.fromtimestamp(int(last_modified) / 1000)
        except (ValueError, OSError):
            modified_time = datetime.utcnow()

        # Read tags and author from .content file
        tags = self._read_document_tags(doc_id)
        author = self._read_document_author(doc_id)

        return RemarkableDocument(
            id=doc_id,
            name=metadata.get("visibleName", "Untitled"),
            document_type=doc_type,
            parent_id=metadata.get("parent"),
            modified_time=modified_time,
            version=1,  # Local cache doesn't track versions the same way
            tags=tags,
            author=author,
        )

    def _read_content_file(self, doc_id: str) -> dict:
        """Read and parse a document's .content file.

        Args:
            doc_id: Document ID

        Returns:
            Parsed content dictionary, or empty dict if not found/invalid
        """
        content_file = self.cache_path / f"{doc_id}.content"
        if not content_file.exists():
            return {}

        try:
            with open(content_file) as f:
                return json.load(f)
        except (json.JSONDecodeError, KeyError):
            return {}

    def _read_document_tags(self, doc_id: str) -> list[str]:
        """Read tags from a document's .content file.

        Args:
            doc_id: Document ID

        Returns:
            List of tag names
        """
        content = self._read_content_file(doc_id)
        if not content:
            return []

        # Tags are stored as a list in the .content file
        # Each tag may be a string or an object with a "name" field
        raw_tags = content.get("tags", [])
        tags = []
        for tag in raw_tags:
            if isinstance(tag, str):
                tags.append(tag)
            elif isinstance(tag, dict) and "name" in tag:
                tags.append(tag["name"])
        return tags

    def _read_document_author(self, doc_id: str) -> Optional[str]:
        """Read author from a document's .content file.

        Args:
            doc_id: Document ID

        Returns:
            Author name(s) as a string, or None if not found
        """
        content = self._read_content_file(doc_id)
        if not content:
            return None

        # Author is stored in documentMetadata.authors as a list
        doc_metadata = content.get("documentMetadata", {})
        authors = doc_metadata.get("authors", [])

        if authors:
            # Join multiple authors with comma
            return ", ".join(authors)
        return None

    def download_document(self, doc_id: str, output_dir: Path) -> Path:
        """Copy a document from the cache to the output directory.

        Args:
            doc_id: Document ID to copy
            output_dir: Directory to save the files

        Returns:
            Path to the copied document directory
        """
        doc_dir = output_dir / doc_id
        doc_dir.mkdir(parents=True, exist_ok=True)

        # Copy the PDF file if it exists
        pdf_source = self.cache_path / f"{doc_id}.pdf"
        if pdf_source.exists():
            shutil.copy2(pdf_source, doc_dir / f"{doc_id}.pdf")

        # Copy the .content file if it exists
        content_source = self.cache_path / f"{doc_id}.content"
        if content_source.exists():
            shutil.copy2(content_source, doc_dir / f"{doc_id}.content")

        # Copy the annotations directory (contains .rm files)
        annotations_source = self.cache_path / doc_id
        if annotations_source.is_dir():
            annotations_dest = doc_dir / doc_id
            if annotations_dest.exists():
                shutil.rmtree(annotations_dest)
            shutil.copytree(annotations_source, annotations_dest)

        return doc_dir

    def get_document_with_highlights(
        self, doc: RemarkableDocument, cache_dir: Path
    ) -> tuple[Optional[Path], list[Path]]:
        """Get document PDF and highlight files from local cache.

        Args:
            doc: Document metadata
            cache_dir: Directory for caching (used for consistency with cloud client)

        Returns:
            Tuple of (pdf_path, list of .rm highlight file paths)
        """
        # Copy document to cache directory
        doc_dir = self.download_document(doc.id, cache_dir)

        # Find the PDF file
        pdf_path: Optional[Path] = None
        pdf_file = doc_dir / f"{doc.id}.pdf"
        if pdf_file.exists():
            pdf_path = pdf_file

        # Find .rm highlight files
        rm_dir = doc_dir / doc.id
        rm_files: list[Path] = []
        if rm_dir.is_dir():
            rm_files = list(rm_dir.glob("*.rm"))

        return pdf_path, rm_files
