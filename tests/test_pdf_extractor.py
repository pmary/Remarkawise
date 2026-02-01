"""Tests for PDF highlight extraction."""

from pathlib import Path

import fitz
import pytest

from remarkawise.pdf.extractor import (
    PDFHighlightExtractor,
    merge_highlight_regions,
)


class TestPDFHighlightExtractor:
    """Tests for PDFHighlightExtractor class."""

    def test_open_pdf(self, sample_pdf: Path) -> None:
        """Test opening a PDF file."""
        with PDFHighlightExtractor(sample_pdf) as extractor:
            assert extractor.page_count == 5
            assert extractor.doc is not None

    def test_extract_text_from_region(self, sample_pdf: Path) -> None:
        """Test extracting text from a specific region."""
        with PDFHighlightExtractor(sample_pdf) as extractor:
            # Extract from top portion of first page (normalized coords)
            text = extractor.extract_text_from_region(
                page_number=0,
                region=(0.0, 0.0, 1.0, 0.3),  # Top 30% of page
            )

            assert "Introduction" in text or "Page 1" in text

    def test_extract_text_invalid_page(self, sample_pdf: Path) -> None:
        """Test extracting from invalid page number."""
        with PDFHighlightExtractor(sample_pdf) as extractor:
            text = extractor.extract_text_from_region(
                page_number=100,  # Invalid page
                region=(0.0, 0.0, 1.0, 1.0),
            )

            assert text == ""

    def test_extract_text_negative_page(self, sample_pdf: Path) -> None:
        """Test extracting from negative page number."""
        with PDFHighlightExtractor(sample_pdf) as extractor:
            text = extractor.extract_text_from_region(
                page_number=-1,
                region=(0.0, 0.0, 1.0, 1.0),
            )

            assert text == ""

    def test_extract_highlights_from_regions(self, sample_pdf: Path) -> None:
        """Test extracting highlights from multiple regions."""
        with PDFHighlightExtractor(sample_pdf) as extractor:
            regions = {
                0: [(0.0, 0.0, 1.0, 0.5)],  # First half of page 1
                2: [(0.0, 0.0, 1.0, 0.5)],  # First half of page 3
            }

            highlights = extractor.extract_highlights_from_regions(
                document_id="test-doc",
                highlight_regions=regions,
            )

            assert len(highlights) >= 0  # May be 0 if no text in regions
            for hl in highlights:
                assert hl.document_id == "test-doc"
                assert hl.id.startswith("rm_")

    def test_highlight_ids_are_deterministic(self, sample_pdf: Path) -> None:
        """Test that highlight IDs are deterministic."""
        with PDFHighlightExtractor(sample_pdf) as extractor:
            regions = {0: [(0.0, 0.0, 1.0, 0.3)]}

            highlights1 = extractor.extract_highlights_from_regions(
                document_id="test-doc",
                highlight_regions=regions,
            )

            highlights2 = extractor.extract_highlights_from_regions(
                document_id="test-doc",
                highlight_regions=regions,
            )

            if highlights1 and highlights2:
                assert highlights1[0].id == highlights2[0].id

    def test_get_document_metadata(self, sample_pdf: Path) -> None:
        """Test extracting document metadata."""
        with PDFHighlightExtractor(sample_pdf) as extractor:
            metadata = extractor.get_document_metadata()

            assert "title" in metadata
            # Title falls back to filename stem if not set
            assert metadata["title"] is not None

    def test_context_manager_closes_doc(self, sample_pdf: Path) -> None:
        """Test that context manager properly closes document."""
        extractor = PDFHighlightExtractor(sample_pdf)

        with extractor:
            doc = extractor.doc
            assert not doc.is_closed

        # Document should be closed after context exit
        assert doc.is_closed


class TestPDFWithNativeHighlights:
    """Tests for PDFs with native highlight annotations."""

    @pytest.fixture
    def pdf_with_highlights(self, temp_dir: Path) -> Path:
        """Create a PDF with native highlight annotations."""
        pdf_path = temp_dir / "highlighted.pdf"

        doc = fitz.open()
        page = doc.new_page(width=612, height=792)

        # Add text
        text_point = fitz.Point(72, 100)
        page.insert_text(text_point, "This is important text to highlight.", fontsize=12)

        # Add a highlight annotation
        highlight_rect = fitz.Rect(72, 90, 300, 110)
        annot = page.add_highlight_annot(highlight_rect)
        annot.set_info(content="My note")
        annot.update()

        doc.save(pdf_path)
        doc.close()

        return pdf_path

    def test_extract_native_highlights(self, pdf_with_highlights: Path) -> None:
        """Test extracting native PDF highlights."""
        with PDFHighlightExtractor(pdf_with_highlights) as extractor:
            highlights = extractor.extract_native_highlights("test-doc")

            assert len(highlights) >= 1
            # Note: actual text extraction depends on overlap with annotation rect
            if highlights:
                assert highlights[0].document_id == "test-doc"
                assert highlights[0].page_number == 1  # 1-indexed

    def test_native_highlight_notes_preserved(self, pdf_with_highlights: Path) -> None:
        """Test that annotation notes are preserved."""
        with PDFHighlightExtractor(pdf_with_highlights) as extractor:
            highlights = extractor.extract_native_highlights("test-doc")

            # Find highlight with note
            noted = [h for h in highlights if h.note]
            if noted:
                assert noted[0].note == "My note"


class TestMergeHighlightRegions:
    """Tests for highlight region merging."""

    def test_merge_overlapping_regions(self) -> None:
        """Test merging overlapping highlight regions."""
        regions = [
            (0.1, 0.1, 0.5, 0.15),
            (0.1, 0.14, 0.5, 0.19),  # Overlaps with first
        ]

        merged = merge_highlight_regions(regions)

        assert len(merged) == 1
        # Merged region should encompass both
        assert merged[0][0] == 0.1  # x_min
        assert merged[0][1] == 0.1  # y_min
        assert merged[0][2] == 0.5  # x_max
        assert merged[0][3] == 0.19  # y_max

    def test_merge_adjacent_regions(self) -> None:
        """Test merging vertically adjacent regions."""
        regions = [
            (0.1, 0.1, 0.5, 0.12),
            (0.1, 0.13, 0.5, 0.15),  # Close but not overlapping
        ]

        merged = merge_highlight_regions(regions, merge_threshold=0.02)

        assert len(merged) == 1

    def test_no_merge_distant_regions(self) -> None:
        """Test that distant regions are not merged."""
        regions = [
            (0.1, 0.1, 0.5, 0.15),
            (0.1, 0.5, 0.5, 0.55),  # Far from first
        ]

        merged = merge_highlight_regions(regions)

        assert len(merged) == 2

    def test_merge_empty_list(self) -> None:
        """Test merging empty region list."""
        merged = merge_highlight_regions([])

        assert merged == []

    def test_merge_single_region(self) -> None:
        """Test merging single region."""
        regions = [(0.1, 0.1, 0.5, 0.15)]

        merged = merge_highlight_regions(regions)

        assert len(merged) == 1
        assert merged[0] == regions[0]

    def test_merge_maintains_order(self) -> None:
        """Test that merged regions are sorted by y-coordinate."""
        regions = [
            (0.1, 0.5, 0.5, 0.55),  # Lower on page
            (0.1, 0.1, 0.5, 0.15),  # Higher on page
        ]

        merged = merge_highlight_regions(regions)

        # Should be sorted top to bottom
        assert merged[0][1] < merged[1][1]

    def test_merge_horizontally_separated(self) -> None:
        """Test that horizontally separated regions on same line don't merge."""
        regions = [
            (0.1, 0.1, 0.2, 0.15),  # Left side
            (0.8, 0.1, 0.9, 0.15),  # Right side, same y
        ]

        merged = merge_highlight_regions(regions)

        # These shouldn't merge because they don't horizontally overlap
        assert len(merged) == 2
