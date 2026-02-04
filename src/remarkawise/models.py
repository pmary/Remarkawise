"""Data models for Remarkawise."""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class DocumentType(str, Enum):
    """Type of document in reMarkable."""

    PDF = "pdf"
    EPUB = "epub"
    NOTEBOOK = "notebook"


class RemarkableDocument(BaseModel):
    """Represents a document from reMarkable."""

    id: str = Field(description="Unique document identifier")
    name: str = Field(description="Document name/title")
    document_type: DocumentType = Field(description="Type of document")
    parent_id: Optional[str] = Field(default=None, description="Parent folder ID")
    modified_time: datetime = Field(description="Last modification time")
    version: int = Field(default=1, description="Document version")
    tags: list[str] = Field(default_factory=list, description="Document tags/labels")
    author: Optional[str] = Field(default=None, description="Document author(s)")

    # Populated after download
    file_path: Optional[str] = Field(default=None, description="Local path to downloaded file")
    has_highlights: bool = Field(default=False, description="Whether document has highlights")


class Highlight(BaseModel):
    """Represents a highlight extracted from a document."""

    id: str = Field(description="Unique highlight identifier")
    document_id: str = Field(description="Source document ID")
    text: str = Field(description="Highlighted text content")
    page_number: int = Field(description="Page number (1-indexed)")
    position: Optional[float] = Field(
        default=None, description="Position on page (0.0-1.0)"
    )
    color: Optional[str] = Field(default=None, description="Highlight color")
    created_at: datetime = Field(
        default_factory=datetime.utcnow, description="When highlight was created"
    )
    note: Optional[str] = Field(default=None, description="Associated note/annotation")


class ReadwiseHighlight(BaseModel):
    """Highlight formatted for Readwise API."""

    text: str = Field(description="The highlighted text")
    title: str = Field(description="Title of the source document")
    author: Optional[str] = Field(default=None, description="Author of the source")
    source_type: str = Field(default="remarkawise", description="Source identifier")
    category: str = Field(default="books", description="Readwise category")
    location: Optional[int] = Field(default=None, description="Location/page number")
    location_type: str = Field(default="page", description="Type of location")
    highlighted_at: Optional[datetime] = Field(
        default=None, description="When the highlight was made"
    )
    note: Optional[str] = Field(default=None, description="Note attached to highlight")
    source_url: Optional[str] = Field(default=None, description="URL to the source")


class SyncState(BaseModel):
    """Tracks sync state for a document."""

    document_id: str = Field(description="reMarkable document ID")
    document_name: str = Field(description="Document name for reference")
    last_synced_at: datetime = Field(description="When document was last synced")
    last_version: int = Field(description="Document version when last synced")
    highlight_count: int = Field(default=0, description="Number of highlights synced")
    checksum: Optional[str] = Field(
        default=None, description="Checksum of synced highlights"
    )


class SyncResult(BaseModel):
    """Result of a sync operation."""

    success: bool = Field(description="Whether sync completed successfully")
    documents_processed: int = Field(default=0, description="Number of documents processed")
    highlights_synced: int = Field(default=0, description="Number of highlights synced")
    highlights_deleted: int = Field(default=0, description="Number of highlights deleted")
    errors: list[str] = Field(default_factory=list, description="Any errors encountered")
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = Field(default=None)
