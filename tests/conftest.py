"""Pytest fixtures and configuration for Remarkawise tests."""

import io
import json
import struct
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock

import fitz
import pytest

from remarkawise.config import Settings
from remarkawise.models import DocumentType, Highlight, RemarkableDocument
from remarkawise.sync.state import StateManager


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def settings(temp_dir: Path) -> Settings:
    """Create test settings with temporary directories."""
    return Settings(
        readwise_access_token="test-readwise-token",
        remarkable_local_cache_path=temp_dir / "remarkable_cache",
        data_dir=temp_dir / "data",
        _env_file=None,  # Don't load .env in tests
    )


@pytest.fixture
def state_manager(temp_dir: Path) -> Generator[StateManager, None, None]:
    """Create a state manager with temporary database."""
    db_path = temp_dir / "test_state.db"
    manager = StateManager(db_path)
    yield manager
    manager.close()


@pytest.fixture
def sample_document() -> RemarkableDocument:
    """Create a sample reMarkable document."""
    return RemarkableDocument(
        id="doc-test-123",
        name="Test Document",
        document_type=DocumentType.PDF,
        modified_time=datetime(2024, 1, 15, 10, 30, 0),
        version=1,
    )


@pytest.fixture
def sample_highlights() -> list[Highlight]:
    """Create sample highlights for testing."""
    return [
        Highlight(
            id="hl-001",
            document_id="doc-test-123",
            text="This is the first highlighted passage from the document.",
            page_number=1,
            position=0.2,
        ),
        Highlight(
            id="hl-002",
            document_id="doc-test-123",
            text="Another important concept that was highlighted.",
            page_number=3,
            position=0.5,
        ),
        Highlight(
            id="hl-003",
            document_id="doc-test-123",
            text="The conclusion contains key insights.",
            page_number=10,
            position=0.8,
            note="Remember this for the exam",
        ),
    ]


@pytest.fixture
def sample_pdf(temp_dir: Path) -> Path:
    """Create a simple test PDF with text content."""
    pdf_path = temp_dir / "test_document.pdf"

    # Create a simple PDF using PyMuPDF
    doc = fitz.open()

    # Add pages with sample text
    pages_content = [
        "Page 1: Introduction\n\nThis is the first highlighted passage from the document.\n\nMore content here.",
        "Page 2: Background\n\nSome background information.",
        "Page 3: Methods\n\nAnother important concept that was highlighted.\n\nDetails about methodology.",
        "Page 4-9: Various content",
        "Page 10: Conclusion\n\nThe conclusion contains key insights.\n\nFinal thoughts.",
    ]

    for content in pages_content:
        page = doc.new_page(width=612, height=792)  # Letter size
        text_point = fitz.Point(72, 72)  # 1 inch margin
        page.insert_text(text_point, content, fontsize=12)

    doc.save(pdf_path)
    doc.close()

    return pdf_path


@pytest.fixture
def sample_rm_file(temp_dir: Path) -> Path:
    """Create a sample .rm file with highlight strokes."""
    rm_path = temp_dir / "0.rm"

    # Create a minimal valid .rm file
    # Header: "reMarkable .lines file, version=X" + padding
    with open(rm_path, "wb") as f:
        # Write header (v5 format)
        header = b"reMarkable .lines file, version=5          "
        f.write(header[:43])

        # Number of pages
        f.write(struct.pack("<I", 1))

        # Page 0: 1 layer
        f.write(struct.pack("<I", 1))

        # Layer 0: 1 stroke (highlighter)
        f.write(struct.pack("<I", 1))

        # Stroke header: pen_type=5 (highlighter), color=0, unknown=0, brush_size
        f.write(struct.pack("<iiiI", 5, 0, 0, 0))  # pen_type, color, unknown, brush_size_raw
        f.write(struct.pack("<f", 2.0))  # unknown float
        f.write(struct.pack("<f", 0.0))  # another float in v5

        # Number of points: 4 points for a horizontal highlight line
        f.write(struct.pack("<I", 4))

        # Points: x, y, speed, direction, width, pressure
        # Creating a horizontal line across the page
        points = [
            (100.0, 200.0, 1.0, 0.0, 2.0, 0.5),
            (300.0, 200.0, 1.0, 0.0, 2.0, 0.5),
            (500.0, 200.0, 1.0, 0.0, 2.0, 0.5),
            (700.0, 200.0, 1.0, 0.0, 2.0, 0.5),
        ]

        for x, y, speed, direction, width, pressure in points:
            f.write(struct.pack("<ffffff", x, y, speed, direction, width, pressure))

    return rm_path


@pytest.fixture
def sample_document_archive(temp_dir: Path, sample_pdf: Path) -> Path:
    """Create a sample reMarkable document archive (ZIP)."""
    archive_path = temp_dir / "doc-test-123.zip"

    with zipfile.ZipFile(archive_path, "w") as zf:
        # Add the PDF
        zf.write(sample_pdf, "test_document.pdf")

        # Add a .content file with page mapping
        content_data = {
            "pages": ["page-uuid-0", "page-uuid-1", "page-uuid-2"],
            "fileType": "pdf",
        }
        zf.writestr("doc-test-123.content", json.dumps(content_data))

        # Add placeholder .rm file
        rm_data = b"reMarkable .lines file, version=5          "
        rm_data += struct.pack("<I", 0)  # 0 pages (empty)
        zf.writestr("page-uuid-0.rm", rm_data)

    return archive_path


# Mock response data for API tests

MOCK_REMARKABLE_DOCS_RESPONSE = [
    {
        "ID": "doc-001",
        "VissibleName": "Research Paper",
        "Type": "DocumentType",
        "fileType": "pdf",
        "Parent": None,
        "ModifiedClient": "2024-01-15T10:30:00Z",
        "Version": 2,
        "BlobURLGet": "https://storage.remarkable.com/blob/doc-001",
    },
    {
        "ID": "doc-002",
        "VissibleName": "Meeting Notes",
        "Type": "DocumentType",
        "fileType": "pdf",
        "Parent": "folder-001",
        "ModifiedClient": "2024-01-14T15:45:00Z",
        "Version": 1,
        "BlobURLGet": "https://storage.remarkable.com/blob/doc-002",
    },
    {
        "ID": "folder-001",
        "VissibleName": "Work",
        "Type": "CollectionType",
        "Parent": None,
        "ModifiedClient": "2024-01-10T09:00:00Z",
        "Version": 1,
    },
]

MOCK_READWISE_BOOKS_RESPONSE = {
    "count": 2,
    "next": None,
    "previous": None,
    "results": [
        {
            "id": 12345,
            "title": "Research Paper",
            "author": "John Doe",
            "category": "books",
            "source": "remarkawise",
            "num_highlights": 5,
        },
        {
            "id": 12346,
            "title": "Another Book",
            "author": "Jane Smith",
            "category": "books",
            "source": "kindle",
            "num_highlights": 10,
        },
    ],
}

MOCK_READWISE_HIGHLIGHTS_RESPONSE = {
    "count": 2,
    "next": None,
    "previous": None,
    "results": [
        {
            "id": 99001,
            "text": "Important insight from the paper",
            "note": None,
            "location": 5,
            "location_type": "page",
            "highlighted_at": "2024-01-15T10:00:00Z",
            "book_id": 12345,
        },
        {
            "id": 99002,
            "text": "Another key point",
            "note": "Review later",
            "location": 12,
            "location_type": "page",
            "highlighted_at": "2024-01-15T11:00:00Z",
            "book_id": 12345,
        },
    ],
}
