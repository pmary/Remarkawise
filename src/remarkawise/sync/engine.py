"""Sync engine orchestrating the reMarkable to Readwise synchronization."""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from remarkawise.config import Settings
from remarkawise.models import (
    DocumentType,
    Highlight,
    RemarkableDocument,
    SyncResult,
    SyncState,
)
from remarkawise.pdf.extractor import PDFHighlightExtractor, merge_highlight_regions
from remarkawise.readwise.client import (
    ReadwiseClient,
    ReadwiseRateLimitError,
    convert_to_readwise_highlights,
)
from remarkawise.remarkable.client import RemarkableClient
from remarkawise.remarkable.parser import HighlightParser
from remarkawise.sync.state import StateManager


class SyncEngine:
    """Orchestrates synchronization between reMarkable and Readwise."""

    def __init__(
        self,
        settings: Settings,
        remarkable_client: Optional[RemarkableClient] = None,
        readwise_client: Optional[ReadwiseClient] = None,
        state_manager: Optional[StateManager] = None,
    ) -> None:
        """Initialize the sync engine.

        Args:
            settings: Application settings
            remarkable_client: Optional pre-configured reMarkable client
            readwise_client: Optional pre-configured Readwise client
            state_manager: Optional pre-configured state manager
        """
        self.settings = settings
        settings.ensure_directories()

        self._rm_client = remarkable_client
        self._rw_client = readwise_client
        self._state_manager = state_manager or StateManager(settings.sync_state_path)
        self._highlight_parser = HighlightParser()

    @property
    def remarkable_client(self) -> RemarkableClient:
        """Get or create the reMarkable client."""
        if self._rm_client is None:
            if not self.settings.remarkable_device_token:
                raise ValueError(
                    "reMarkable device token not configured. "
                    "Run 'remarkawise auth remarkable' first."
                )
            self._rm_client = RemarkableClient(self.settings.remarkable_device_token)
        return self._rm_client

    @property
    def readwise_client(self) -> ReadwiseClient:
        """Get or create the Readwise client."""
        if self._rw_client is None:
            if not self.settings.readwise_access_token:
                raise ValueError(
                    "Readwise access token not configured. "
                    "Set READWISE_ACCESS_TOKEN in .env file."
                )
            self._rw_client = ReadwiseClient(self.settings.readwise_access_token)
        return self._rw_client

    def sync(
        self,
        force: bool = False,
        document_ids: Optional[list[str]] = None,
    ) -> SyncResult:
        """Run the synchronization process.

        Args:
            force: Force re-sync of all documents, ignoring state
            document_ids: Optional list of specific document IDs to sync

        Returns:
            SyncResult with statistics and any errors
        """
        result = SyncResult(success=False, started_at=datetime.utcnow())
        errors: list[str] = []

        try:
            # Get list of documents from reMarkable
            documents = self.remarkable_client.list_documents()

            # Filter to PDFs only (for now)
            pdf_docs = [d for d in documents if d.document_type == DocumentType.PDF]

            # Filter to specific documents if requested
            if document_ids:
                pdf_docs = [d for d in pdf_docs if d.id in document_ids]

            # Filter to documents that need syncing
            if not force:
                pdf_docs = [
                    d
                    for d in pdf_docs
                    if self._state_manager.needs_sync(d.id, d.version)
                ]

            total_highlights = 0

            for doc in pdf_docs:
                try:
                    highlights_synced = self._sync_document(doc, force=force)
                    total_highlights += highlights_synced
                    result.documents_processed += 1
                except Exception as e:
                    error_msg = f"Error syncing '{doc.name}': {str(e)}"
                    errors.append(error_msg)

            result.highlights_synced = total_highlights
            result.success = len(errors) == 0
            result.errors = errors

        except Exception as e:
            errors.append(f"Sync failed: {str(e)}")
            result.errors = errors

        result.completed_at = datetime.utcnow()
        return result

    def _sync_document(
        self, doc: RemarkableDocument, force: bool = False
    ) -> int:
        """Sync a single document.

        Args:
            doc: Document to sync
            force: Force re-sync ignoring state

        Returns:
            Number of highlights synced
        """
        # Download document and get PDF + annotation files
        pdf_path, rm_files = self.remarkable_client.get_document_with_highlights(
            doc, self.settings.cache_dir
        )

        if not pdf_path:
            return 0

        # Extract highlights
        highlights = self._extract_highlights(doc, pdf_path, rm_files)

        if not highlights:
            return 0

        # Filter to unsynced highlights (unless forcing)
        if not force:
            highlights = self._state_manager.get_unsynced_highlights(doc.id, highlights)

        if not highlights:
            return 0

        # Get document metadata
        with PDFHighlightExtractor(pdf_path) as extractor:
            metadata = extractor.get_document_metadata()

        # Convert to Readwise format
        rw_highlights = convert_to_readwise_highlights(
            highlights,
            document_title=metadata.get("title") or doc.name,
            author=metadata.get("author"),
        )

        # Upload to Readwise with retry on rate limit
        self._upload_with_retry(rw_highlights)

        # Update sync state
        self._state_manager.mark_highlights_synced(doc.id, highlights)

        checksum = self._state_manager.calculate_highlights_checksum(highlights)
        state = SyncState(
            document_id=doc.id,
            document_name=doc.name,
            last_synced_at=datetime.utcnow(),
            last_version=doc.version,
            highlight_count=len(highlights),
            checksum=checksum,
        )
        self._state_manager.update_sync_state(state)

        return len(highlights)

    def _extract_highlights(
        self,
        doc: RemarkableDocument,
        pdf_path: Path,
        rm_files: list[Path],
    ) -> list[Highlight]:
        """Extract highlights from a document.

        Combines highlights from:
        1. reMarkable .rm annotation files
        2. Native PDF highlight annotations

        Args:
            doc: Document metadata
            pdf_path: Path to the PDF file
            rm_files: Paths to .rm annotation files

        Returns:
            Combined list of highlights
        """
        highlights: list[Highlight] = []

        # Extract from .rm files
        for rm_file in rm_files:
            try:
                # Get page number from .rm file
                page_num = self._get_page_number_for_rm_file(rm_file, doc.id)

                if page_num is None:
                    continue

                # Parse highlight regions
                regions = self._highlight_parser.extract_highlight_regions(rm_file)

                if not regions:
                    continue

                # Merge nearby regions and extract text
                with PDFHighlightExtractor(pdf_path) as extractor:
                    for page, page_regions in regions.items():
                        merged = merge_highlight_regions(page_regions)
                        page_highlights = extractor.extract_highlights_from_regions(
                            doc.id, {page_num: merged}
                        )
                        highlights.extend(page_highlights)

            except Exception:
                # Skip problematic .rm files
                continue

        # Also extract native PDF highlights
        with PDFHighlightExtractor(pdf_path) as extractor:
            native_highlights = extractor.extract_native_highlights(doc.id)
            highlights.extend(native_highlights)

        # Deduplicate by text content
        seen_texts: set[str] = set()
        unique_highlights: list[Highlight] = []

        for hl in highlights:
            text_key = hl.text.strip().lower()[:100]
            if text_key not in seen_texts:
                seen_texts.add(text_key)
                unique_highlights.append(hl)

        return unique_highlights

    def _get_page_number_for_rm_file(
        self, rm_file: Path, doc_id: str
    ) -> Optional[int]:
        """Determine the page number for an .rm file.

        reMarkable stores annotations in files named by UUID.
        The mapping is in the .content file.

        Args:
            rm_file: Path to the .rm file
            doc_id: Document ID

        Returns:
            0-indexed page number, or None if not determinable
        """
        # Look for .content file in parent directory
        content_file = rm_file.parent / f"{doc_id}.content"

        if not content_file.exists():
            # Try alternate location
            content_file = rm_file.parent.parent / f"{doc_id}.content"

        if content_file.exists():
            try:
                with open(content_file) as f:
                    content = json.load(f)

                # Pages are listed in order
                pages = content.get("pages", [])
                rm_uuid = rm_file.stem

                for idx, page_uuid in enumerate(pages):
                    if page_uuid == rm_uuid:
                        return idx

            except (json.JSONDecodeError, KeyError):
                pass

        # Fallback: try parsing filename as number
        try:
            return int(rm_file.stem)
        except ValueError:
            pass

        # Default to page 0 if we can't determine
        return 0

    def _upload_with_retry(
        self,
        highlights: list,
        max_retries: int = 3,
    ) -> None:
        """Upload highlights with retry on rate limit.

        Args:
            highlights: Highlights to upload
            max_retries: Maximum retry attempts
        """
        for attempt in range(max_retries):
            try:
                self.readwise_client.create_highlights(highlights)
                return
            except ReadwiseRateLimitError as e:
                if attempt < max_retries - 1:
                    time.sleep(e.retry_after)
                else:
                    raise

    def get_status(self) -> dict:
        """Get current sync status.

        Returns:
            Dictionary with sync statistics
        """
        stats = self._state_manager.get_stats()
        synced_docs = self._state_manager.get_all_synced_documents()

        return {
            "stats": stats,
            "recent_syncs": [
                {
                    "name": d.document_name,
                    "synced_at": d.last_synced_at.isoformat(),
                    "highlights": d.highlight_count,
                }
                for d in synced_docs[:10]
            ],
        }

    def close(self) -> None:
        """Close all connections."""
        if self._rm_client:
            self._rm_client.close()
        if self._rw_client:
            self._rw_client.close()
        if self._state_manager:
            self._state_manager.close()
