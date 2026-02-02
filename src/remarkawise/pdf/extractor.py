"""PDF highlight extraction using PyMuPDF (fitz).

This module extracts text from PDF regions that correspond to highlight
annotations from reMarkable devices.
"""

import hashlib
from pathlib import Path
from typing import Optional
from uuid import uuid4

import fitz  # PyMuPDF

from remarkawise.models import Highlight


class PDFHighlightExtractor:
    """Extracts highlighted text from PDFs using coordinate regions."""

    def __init__(self, pdf_path: Path) -> None:
        """Initialize with a PDF file path.

        Args:
            pdf_path: Path to the PDF file
        """
        self.pdf_path = pdf_path
        self._doc: Optional[fitz.Document] = None

    def __enter__(self) -> "PDFHighlightExtractor":
        self._doc = fitz.open(self.pdf_path)
        return self

    def __exit__(self, *args: object) -> None:
        if self._doc:
            self._doc.close()

    @property
    def doc(self) -> fitz.Document:
        """Get the PDF document, opening it if necessary."""
        if self._doc is None:
            self._doc = fitz.open(self.pdf_path)
        return self._doc

    @property
    def page_count(self) -> int:
        """Get the number of pages in the PDF."""
        return len(self.doc)

    def extract_text_from_region(
        self,
        page_number: int,
        region: tuple[float, float, float, float],
    ) -> str:
        """Extract text from a normalized region on a page.

        Args:
            page_number: 0-indexed page number
            region: Normalized bounding box (x0, y0, x1, y1) with values 0-1

        Returns:
            Extracted text from the region
        """
        if page_number < 0 or page_number >= len(self.doc):
            return ""

        page = self.doc[page_number]
        page_rect = page.rect

        # Convert normalized coordinates to page coordinates
        x0 = region[0] * page_rect.width
        y0 = region[1] * page_rect.height
        x1 = region[2] * page_rect.width
        y1 = region[3] * page_rect.height

        # Create a rectangle for text extraction
        rect = fitz.Rect(x0, y0, x1, y1)

        # Extract text from the rectangle
        text = page.get_text("text", clip=rect)

        return text.strip()

    def extract_highlights_from_regions(
        self,
        document_id: str,
        highlight_regions: dict[int, list[tuple[float, float, float, float]]],
    ) -> list[Highlight]:
        """Extract highlight text from multiple regions.

        Args:
            document_id: ID of the source document
            highlight_regions: Dictionary mapping page numbers to lists of
                             normalized bounding boxes

        Returns:
            List of Highlight objects with extracted text
        """
        highlights: list[Highlight] = []

        for page_num, regions in highlight_regions.items():
            for region in regions:
                text = self.extract_text_from_region(page_num, region)

                if not text:
                    continue

                # Generate a deterministic ID based on content
                highlight_id = self._generate_highlight_id(document_id, page_num, text)

                # Calculate position as fraction of page height
                position = (region[1] + region[3]) / 2  # Average of y coordinates

                highlight = Highlight(
                    id=highlight_id,
                    document_id=document_id,
                    text=text,
                    page_number=page_num + 1,  # Convert to 1-indexed
                    position=position,
                )
                highlights.append(highlight)

        return highlights

    def extract_native_highlights(self, document_id: str) -> list[Highlight]:
        """Extract highlights that are natively embedded in the PDF.

        Some PDFs have highlight annotations added by other PDF readers.
        This extracts those in addition to reMarkable highlights.

        Args:
            document_id: ID of the source document

        Returns:
            List of Highlight objects
        """
        highlights: list[Highlight] = []

        for page_num in range(len(self.doc)):
            page = self.doc[page_num]

            # Get all annotations on the page
            for annot in page.annots() or []:
                # Check if it's a highlight annotation (type 8)
                if annot.type[0] != 8:
                    continue

                # Get the highlighted text
                rect = annot.rect
                text = page.get_text("text", clip=rect).strip()

                if not text:
                    continue

                highlight_id = self._generate_highlight_id(document_id, page_num, text)

                # Get any associated comment/note
                note = annot.info.get("content", "")

                highlight = Highlight(
                    id=highlight_id,
                    document_id=document_id,
                    text=text,
                    page_number=page_num + 1,
                    position=rect.y0 / page.rect.height,
                    note=note if note else None,
                )
                highlights.append(highlight)

        return highlights

    def _generate_highlight_id(
        self, document_id: str, page_number: int, text: str
    ) -> str:
        """Generate a deterministic ID for a highlight.

        This ensures the same highlight gets the same ID across syncs.
        """
        content = f"{document_id}:{page_number}:{text[:100]}"
        hash_value = hashlib.sha256(content.encode()).hexdigest()[:16]
        return f"rm_{hash_value}"

    def get_document_metadata(self) -> dict[str, Optional[str]]:
        """Extract metadata from the PDF.

        Returns:
            Dictionary with title, author, and other metadata.
            Title may be None if not present in PDF metadata.
        """
        metadata = self.doc.metadata

        return {
            "title": metadata.get("title") or None,
            "author": metadata.get("author"),
            "subject": metadata.get("subject"),
            "creator": metadata.get("creator"),
        }


def merge_highlight_regions(
    regions: list[tuple[float, float, float, float]],
    merge_threshold: float = 0.02,
) -> list[tuple[float, float, float, float]]:
    """Merge overlapping or adjacent highlight regions.

    When highlighting multiple lines, reMarkable may create separate
    strokes that should be merged into a single highlight.

    Args:
        regions: List of normalized bounding boxes
        merge_threshold: Maximum gap to merge (as fraction of page)

    Returns:
        Merged list of bounding boxes
    """
    if not regions:
        return []

    # Sort by y-coordinate (top to bottom)
    sorted_regions = sorted(regions, key=lambda r: (r[1], r[0]))

    merged: list[tuple[float, float, float, float]] = []
    current = sorted_regions[0]

    for region in sorted_regions[1:]:
        # Check if regions are vertically adjacent and horizontally overlapping
        vertical_gap = region[1] - current[3]
        horizontal_overlap = min(current[2], region[2]) - max(current[0], region[0])

        if vertical_gap <= merge_threshold and horizontal_overlap > -merge_threshold:
            # Merge the regions
            current = (
                min(current[0], region[0]),
                min(current[1], region[1]),
                max(current[2], region[2]),
                max(current[3], region[3]),
            )
        else:
            merged.append(current)
            current = region

    merged.append(current)
    return merged
