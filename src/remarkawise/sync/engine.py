"""Sync engine orchestrating the reMarkable to Readwise synchronization."""

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from remarkawise.config import DataSource, Settings
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
from remarkawise.remarkable.local_cache import LocalCacheClient
from remarkawise.remarkable.parser import HighlightParser
from remarkawise.sync.state import StateManager

# Type alias for both client types
RemarkableClientType = RemarkableClient | LocalCacheClient


class SyncEngine:
    """Orchestrates synchronization between reMarkable and Readwise."""

    def __init__(
        self,
        settings: Settings,
        remarkable_client: Optional[RemarkableClientType] = None,
        readwise_client: Optional[ReadwiseClient] = None,
        state_manager: Optional[StateManager] = None,
        verbose: bool = False,
    ) -> None:
        """Initialize the sync engine.

        Args:
            settings: Application settings
            remarkable_client: Optional pre-configured reMarkable client (cloud or local)
            readwise_client: Optional pre-configured Readwise client
            state_manager: Optional pre-configured state manager
            verbose: Enable verbose logging
        """
        self.settings = settings
        self.verbose = verbose
        settings.ensure_directories()

        self._rm_client = remarkable_client
        self._rw_client = readwise_client
        self._state_manager = state_manager or StateManager(settings.sync_state_path)
        self._highlight_parser = HighlightParser(verbose=verbose)

    def _log(self, message: str) -> None:
        """Print a message if verbose mode is enabled."""
        if self.verbose:
            print(f"  [DEBUG] {message}")

    @property
    def remarkable_client(self) -> RemarkableClientType:
        """Get or create the reMarkable client based on configured data source."""
        if self._rm_client is None:
            if self.settings.remarkable_source == DataSource.LOCAL:
                self._rm_client = LocalCacheClient(
                    cache_path=self.settings.remarkable_local_cache_path
                )
            else:
                if not self.settings.remarkable_device_token:
                    raise ValueError(
                        "reMarkable device token not configured. "
                        "Run 'remarkawise auth remarkable' first, "
                        "or set REMARKABLE_SOURCE=local to use local cache."
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
        tag: Optional[str] = None,
    ) -> SyncResult:
        """Run the synchronization process.

        Args:
            force: Force re-sync of all documents, ignoring state
            document_ids: Optional list of specific document IDs to sync
            tag: Optional tag to filter documents by

        Returns:
            SyncResult with statistics and any errors
        """
        result = SyncResult(success=False, started_at=datetime.utcnow())
        errors: list[str] = []

        try:
            # Get list of documents from reMarkable
            documents = self.remarkable_client.list_documents()

            # Filter to supported document types (PDFs and EPUBs)
            # EPUBs are rendered as PDFs by reMarkable, so we can extract highlights from them
            supported_types = {DocumentType.PDF, DocumentType.EPUB}
            syncable_docs = [d for d in documents if d.document_type in supported_types]

            # Filter to specific documents if requested
            if document_ids:
                syncable_docs = [d for d in syncable_docs if d.id in document_ids]

            # Filter by tag if specified
            if tag:
                tag_lower = tag.lower()
                syncable_docs = [
                    d for d in syncable_docs
                    if any(t.lower() == tag_lower for t in d.tags)
                ]
                self._log(f"Filtered to {len(syncable_docs)} documents with tag '{tag}'")

            # Note: We don't filter by version because adding highlights doesn't change
            # the document version on reMarkable. Instead, we check for unsynced
            # highlights in _sync_document.

            total_highlights = 0
            total_deleted = 0

            for doc in syncable_docs:
                try:
                    synced, deleted = self._sync_document(doc, force=force)
                    total_highlights += synced
                    total_deleted += deleted
                    result.documents_processed += 1
                except Exception as e:
                    error_msg = f"Error syncing '{doc.name}': {str(e)}"
                    errors.append(error_msg)

            result.highlights_synced = total_highlights
            result.highlights_deleted = total_deleted
            result.success = len(errors) == 0
            result.errors = errors

        except Exception as e:
            errors.append(f"Sync failed: {str(e)}")
            result.errors = errors

        result.completed_at = datetime.utcnow()
        return result

    def _sync_document(
        self, doc: RemarkableDocument, force: bool = False
    ) -> tuple[int, int]:
        """Sync a single document.

        Args:
            doc: Document to sync
            force: Force re-sync ignoring state

        Returns:
            Tuple of (highlights synced, highlights deleted)
        """
        self._log(f"Processing document: {doc.name} ({doc.id})")

        # Download document and get PDF + annotation files
        pdf_path, rm_files = self.remarkable_client.get_document_with_highlights(
            doc, self.settings.cache_dir
        )

        self._log(f"  PDF path: {pdf_path}")
        self._log(f"  Found {len(rm_files)} .rm files")

        if not pdf_path:
            self._log("  No PDF found, skipping")
            return 0, 0

        # Extract all current highlights from the document
        all_highlights = self._extract_highlights(doc, pdf_path, rm_files)

        self._log(f"  Extracted {len(all_highlights)} highlights")

        # Detect deleted highlights (synced before but no longer on device)
        deleted_count = self._sync_deletions(doc.id, all_highlights)

        if not all_highlights:
            self._log("  No highlights found")
            return 0, deleted_count

        # Filter to unsynced highlights (unless forcing)
        highlights_to_sync = all_highlights
        if not force:
            highlights_to_sync = self._state_manager.get_unsynced_highlights(
                doc.id, all_highlights
            )

        if not highlights_to_sync:
            self._log("  No new highlights to sync")
            return 0, deleted_count

        # Get document metadata
        with PDFHighlightExtractor(pdf_path) as extractor:
            metadata = extractor.get_document_metadata()

        # Determine Readwise category from document tags
        category = self._get_category_from_tags(doc.tags)
        self._log(f"  Category: {category}")

        # Convert to Readwise format
        rw_highlights = convert_to_readwise_highlights(
            highlights_to_sync,
            document_title=metadata.get("title") or doc.name,
            author=metadata.get("author"),
            category=category,
        )

        # Upload to Readwise with retry on rate limit
        readwise_ids = self._upload_with_retry(rw_highlights)
        self._log(f"  Captured {len(readwise_ids)} Readwise IDs for deletion tracking")

        # Update sync state with Readwise IDs
        self._state_manager.mark_highlights_synced(
            doc.id, highlights_to_sync, readwise_ids
        )

        # Update document sync state
        checksum = self._state_manager.calculate_highlights_checksum(all_highlights)
        state = SyncState(
            document_id=doc.id,
            document_name=doc.name,
            last_synced_at=datetime.utcnow(),
            last_version=doc.version,
            highlight_count=len(all_highlights),
            checksum=checksum,
        )
        self._state_manager.update_sync_state(state)

        return len(highlights_to_sync), deleted_count

    def _sync_deletions(
        self, document_id: str, current_highlights: list[Highlight]
    ) -> int:
        """Sync highlight deletions to Readwise.

        Args:
            document_id: Document ID
            current_highlights: Current highlights on the device

        Returns:
            Number of highlights deleted
        """
        deleted_highlights = self._state_manager.get_deleted_highlights(
            document_id, current_highlights
        )

        if not deleted_highlights:
            return 0

        self._log(f"  Found {len(deleted_highlights)} deleted highlights")

        # Delete from Readwise
        deleted_count = 0
        highlight_ids_to_remove: list[str] = []

        for hl in deleted_highlights:
            readwise_id = hl.get("readwise_id")
            highlight_id = hl["highlight_id"]

            if readwise_id:
                try:
                    if self.readwise_client.delete_highlight(readwise_id):
                        deleted_count += 1
                        self._log(f"    Deleted highlight {highlight_id} from Readwise")
                    else:
                        self._log(
                            f"    Highlight {highlight_id} not found in Readwise (already deleted?)"
                        )
                except Exception as e:
                    self._log(f"    Failed to delete highlight {highlight_id}: {e}")
                    continue
            else:
                self._log(
                    f"    Highlight {highlight_id} has no Readwise ID, removing from tracking only"
                )

            highlight_ids_to_remove.append(highlight_id)

        # Remove from local tracking
        if highlight_ids_to_remove:
            self._state_manager.remove_highlights(highlight_ids_to_remove)

        return deleted_count

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
                self._log(f"  Processing .rm file: {rm_file.name}")

                # Get page number from .rm file
                page_num = self._get_page_number_for_rm_file(rm_file, doc.id)
                self._log(f"    Page number: {page_num}")

                if page_num is None:
                    self._log("    Could not determine page number, skipping")
                    continue

                # First, try to extract glyph highlights (Paper Pro format with pre-extracted text)
                glyph_highlights = self._highlight_parser.extract_glyph_highlights(rm_file)
                if glyph_highlights:
                    self._log(f"    Found {len(glyph_highlights)} glyph highlights (Paper Pro format)")
                    for gh in glyph_highlights:
                        # Generate deterministic ID
                        content = f"{doc.id}:{page_num}:{gh.text[:100]}"
                        hash_value = hashlib.sha256(content.encode()).hexdigest()[:16]
                        highlight_id = f"rm_{hash_value}"

                        highlight = Highlight(
                            id=highlight_id,
                            document_id=doc.id,
                            text=gh.text,
                            page_number=page_num + 1,  # Convert to 1-indexed
                            position=0.5,  # Default position
                        )
                        highlights.append(highlight)
                        text_preview = gh.text[:50] + "..." if len(gh.text) > 50 else gh.text
                        self._log(f"      - \"{text_preview}\"")
                    # If we found glyph highlights, skip stroke-based extraction for this file
                    continue

                # Fall back to stroke-based highlight extraction (older format)
                # Parse highlight regions
                regions = self._highlight_parser.extract_highlight_regions(rm_file)
                self._log(f"    Found regions on {len(regions)} pages: {list(regions.keys())}")

                if not regions:
                    self._log("    No highlight regions found in .rm file")
                    continue

                # Merge nearby regions and extract text
                with PDFHighlightExtractor(pdf_path) as extractor:
                    for page, page_regions in regions.items():
                        self._log(f"    Page {page} has {len(page_regions)} regions")
                        merged = merge_highlight_regions(page_regions)
                        self._log(f"    After merge: {len(merged)} regions")
                        page_highlights = extractor.extract_highlights_from_regions(
                            doc.id, {page_num: merged}
                        )
                        self._log(f"    Extracted {len(page_highlights)} highlights with text")
                        for hl in page_highlights:
                            self._log(f"      - \"{hl.text[:50]}...\"" if len(hl.text) > 50 else f"      - \"{hl.text}\"")
                        highlights.extend(page_highlights)

            except Exception as e:
                self._log(f"    Error processing .rm file: {e}")
                # Skip problematic .rm files
                continue

        # Also extract native PDF highlights
        self._log("  Checking for native PDF highlights...")
        with PDFHighlightExtractor(pdf_path) as extractor:
            native_highlights = extractor.extract_native_highlights(doc.id)
            self._log(f"  Found {len(native_highlights)} native PDF highlights")
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

    def _get_category_from_tags(self, tags: list[str]) -> str:
        """Determine Readwise category from document tags.

        Supported tags (case-insensitive):
        - 'article' -> 'articles' (Readwise Articles tab)
        - 'book' -> 'books' (Readwise Books tab)

        Args:
            tags: List of document tags

        Returns:
            Readwise category string
        """
        tags_lower = [t.lower() for t in tags]
        if "article" in tags_lower:
            return "articles"
        if "book" in tags_lower:
            return "books"
        return "books"  # default

    def _upload_with_retry(
        self,
        highlights: list,
        max_retries: int = 3,
    ) -> dict[str, int]:
        """Upload highlights with retry on rate limit.

        Args:
            highlights: Highlights to upload
            max_retries: Maximum retry attempts

        Returns:
            Mapping of text_hash to Readwise highlight ID
        """
        for attempt in range(max_retries):
            try:
                result = self.readwise_client.create_highlights(highlights)
                return result.get("readwise_ids", {})
            except ReadwiseRateLimitError as e:
                if attempt < max_retries - 1:
                    time.sleep(e.retry_after)
                else:
                    raise
        return {}

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
