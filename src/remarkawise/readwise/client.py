"""Readwise Reader API client.

Readwise provides an API for importing highlights from various sources.
Documentation: https://readwise.io/api_deets
"""

from datetime import datetime
from typing import Optional, TypedDict

import httpx

from remarkawise.logging import get_logger
from remarkawise.models import Highlight, ReadwiseHighlight
from remarkawise.utils import generate_text_hash

logger = get_logger("readwise")


class CreateHighlightsResult(TypedDict):
    """Result from creating highlights."""

    created: int
    readwise_ids: dict[str, int]


class ReadwiseBook(TypedDict, total=False):
    """Book/source information from Readwise API."""

    id: int
    title: str
    author: str
    category: str
    source: str
    num_highlights: int
    cover_image_url: str


class ReadwiseHighlightInfo(TypedDict, total=False):
    """Highlight information from Readwise API."""

    id: int
    text: str
    note: str
    location: int
    location_type: str
    highlighted_at: str
    book_id: int


class ReadwiseAPIError(Exception):
    """Error from Readwise API."""

    pass


class ReadwiseRateLimitError(ReadwiseAPIError):
    """Rate limit exceeded."""

    def __init__(self, retry_after: int = 60) -> None:
        self.retry_after = retry_after
        super().__init__(f"Rate limit exceeded. Retry after {retry_after} seconds.")


class ReadwiseClient:
    """Client for Readwise Reader API."""

    BASE_URL = "https://readwise.io/api/v2"
    READER_API_URL = "https://readwise.io/api/v3"

    def __init__(self, access_token: str) -> None:
        """Initialize the client.

        Args:
            access_token: Readwise API access token from readwise.io/access_token
        """
        self.access_token = access_token
        self._client = httpx.Client(
            timeout=30.0,
            headers={
                "Authorization": f"Token {access_token}",
                "Content-Type": "application/json",
            },
        )

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self) -> "ReadwiseClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def verify_token(self) -> bool:
        """Verify that the access token is valid.

        Returns:
            True if token is valid, False otherwise
        """
        try:
            response = self._client.get(f"{self.BASE_URL}/auth")
            return response.status_code == 204
        except httpx.HTTPError:
            return False

    def _check_rate_limit(self, response: httpx.Response) -> None:
        """Check response for rate limit and raise if exceeded.

        Args:
            response: HTTP response to check

        Raises:
            ReadwiseRateLimitError: If rate limit is exceeded
        """
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 60))
            raise ReadwiseRateLimitError(retry_after)

    def _parse_highlights_response(
        self,
        data: dict | list,
        text_hashes: dict[str, str],
    ) -> tuple[list[dict], list[int]]:
        """Parse API response to extract highlights and book IDs.

        Handles multiple response formats from the Readwise API:
        - List of books with modified_highlights
        - List of highlights directly
        - Dict with modified_highlights or highlights key

        Args:
            data: Parsed JSON response
            text_hashes: Mapping of text content to hash

        Returns:
            Tuple of (created_highlights list, book_ids list)
        """
        created_highlights: list[dict] = []
        book_ids: list[int] = []

        logger.debug(f"Response type: {type(data).__name__}")
        if isinstance(data, dict):
            logger.debug(f"Response keys: {list(data.keys())}")
        elif isinstance(data, list) and data:
            logger.debug(f"First item keys: {list(data[0].keys()) if data[0] else 'empty'}")

        if isinstance(data, list):
            # Check if it's a list of books (each with modified_highlights)
            if data and isinstance(data[0], dict) and "modified_highlights" in data[0]:
                for book in data:
                    book_id = book.get("id")
                    if book_id:
                        book_ids.append(book_id)
                    modified = book.get("modified_highlights", [])
                    created_highlights.extend(modified)
            else:
                created_highlights = data
        elif isinstance(data, dict):
            created_highlights = (
                data.get("modified_highlights")
                or data.get("highlights")
                or []
            )

        if created_highlights:
            logger.debug(f"Found {len(created_highlights)} items in response")
            first = created_highlights[0]
            if isinstance(first, dict):
                logger.debug(f"First item keys: {list(first.keys())}")
            else:
                logger.debug(f"First item type: {type(first).__name__}")

        return created_highlights, book_ids

    def _extract_readwise_ids(
        self,
        created_highlights: list,
        book_ids: list[int],
        text_hashes: dict[str, str],
    ) -> dict[str, int]:
        """Extract Readwise IDs from created highlights.

        Handles both full highlight objects and ID-only responses.

        Args:
            created_highlights: List of created highlights or IDs
            book_ids: List of book IDs for fallback fetching
            text_hashes: Mapping of text content to hash

        Returns:
            Mapping of text_hash to Readwise highlight ID
        """
        readwise_ids: dict[str, int] = {}

        if not created_highlights:
            return readwise_ids

        # Check if we got full objects or just IDs
        got_ids_only = isinstance(created_highlights[0], int)

        if got_ids_only and book_ids:
            # API returned just highlight IDs - fetch full data from book
            logger.debug(f"Got {len(created_highlights)} highlight IDs, fetching details...")
            for book_id in book_ids:
                try:
                    book_highlights = self.get_highlights_for_book(book_id)
                    for rw_hl in book_highlights:
                        text = rw_hl.get("text", "")
                        rw_id = rw_hl.get("id")
                        if text and rw_id and text in text_hashes:
                            readwise_ids[text_hashes[text]] = rw_id
                except (ReadwiseAPIError, ReadwiseRateLimitError, httpx.HTTPError) as e:
                    logger.debug(f"Failed to fetch highlights for book {book_id}: {e}")
        else:
            # Got full highlight objects - extract directly
            for rw_hl in created_highlights:
                if isinstance(rw_hl, dict):
                    text = rw_hl.get("text", "")
                    rw_id = rw_hl.get("id")
                    if text and rw_id and text in text_hashes:
                        readwise_ids[text_hashes[text]] = rw_id

        return readwise_ids

    def create_highlights(
        self,
        highlights: list[ReadwiseHighlight],
    ) -> CreateHighlightsResult:
        """Create multiple highlights in Readwise.

        Args:
            highlights: List of highlights to create

        Returns:
            CreateHighlightsResult with created count and readwise_ids mapping

        Raises:
            ReadwiseAPIError: If the API request fails
            ReadwiseRateLimitError: If rate limit is exceeded
        """
        if not highlights:
            return CreateHighlightsResult(created=0, readwise_ids={})

        # Build mapping of text to hash for response parsing
        text_hashes = {h.text: generate_text_hash(h.text) for h in highlights}

        # Format highlights for the API
        payload = {"highlights": self._format_highlights_payload(highlights)}

        response = self._client.post(f"{self.BASE_URL}/highlights/", json=payload)

        self._check_rate_limit(response)

        if response.status_code not in (200, 201):
            raise ReadwiseAPIError(
                f"Failed to create highlights: {response.status_code} - {response.text}"
            )

        # Parse response to get Readwise IDs
        readwise_ids: dict[str, int] = {}
        try:
            data = response.json()
            created_highlights, book_ids = self._parse_highlights_response(data, text_hashes)
            readwise_ids = self._extract_readwise_ids(created_highlights, book_ids, text_hashes)
        except (ValueError, KeyError) as e:
            logger.warning(f"Failed to parse Readwise response: {e}")
            logger.debug(f"Response: {response.text[:500]}")

        return CreateHighlightsResult(created=len(highlights), readwise_ids=readwise_ids)

    def _format_highlights_payload(self, highlights: list[ReadwiseHighlight]) -> list[dict]:
        """Format highlights for the API payload.

        Args:
            highlights: List of highlights to format

        Returns:
            List of highlight dictionaries ready for API
        """
        formatted = []
        for h in highlights:
            hl_dict = {
                "text": h.text,
                "title": h.title,
                "author": h.author,
                "source_type": h.source_type,
                "category": h.category,
                "location": h.location,
                "location_type": h.location_type,
                "highlighted_at": h.highlighted_at.isoformat() if h.highlighted_at else None,
                "note": h.note,
                "source_url": h.source_url,
            }
            # Remove None values
            formatted.append({k: v for k, v in hl_dict.items() if v is not None})
        return formatted

    def delete_highlight(self, highlight_id: int) -> bool:
        """Delete a highlight from Readwise.

        Args:
            highlight_id: Readwise highlight ID to delete

        Returns:
            True if deleted successfully, False if not found

        Raises:
            ReadwiseAPIError: If the API request fails
            ReadwiseRateLimitError: If rate limit is exceeded
        """
        response = self._client.delete(f"{self.BASE_URL}/highlights/{highlight_id}")

        self._check_rate_limit(response)

        if response.status_code == 404:
            return False

        if response.status_code not in (200, 204):
            raise ReadwiseAPIError(
                f"Failed to delete highlight: {response.status_code} - {response.text}"
            )

        return True

    def delete_highlights(self, highlight_ids: list[int]) -> int:
        """Delete multiple highlights from Readwise.

        Args:
            highlight_ids: List of Readwise highlight IDs to delete

        Returns:
            Number of highlights successfully deleted
        """
        deleted = 0
        for hid in highlight_ids:
            if self.delete_highlight(hid):
                deleted += 1
        return deleted

    def get_books(self, category: Optional[str] = None) -> list[ReadwiseBook]:
        """Get list of books/sources in Readwise.

        Args:
            category: Filter by category (books, articles, tweets, etc.)

        Returns:
            List of ReadwiseBook objects
        """
        params = {"category": category} if category else {}
        books = []
        next_url: Optional[str] = f"{self.BASE_URL}/books/"

        while next_url:
            response = self._client.get(next_url, params=params)
            self._check_rate_limit(response)

            if response.status_code != 200:
                raise ReadwiseAPIError(
                    f"Failed to get books: {response.status_code} - {response.text}"
                )

            data = response.json()
            books.extend(data.get("results", []))
            next_url = data.get("next")
            params = {}  # Clear params for pagination URL

        return books

    def find_book_by_title(self, title: str) -> Optional[ReadwiseBook]:
        """Find a book by its title.

        Args:
            title: Book title to search for

        Returns:
            ReadwiseBook if found, None otherwise
        """
        books = self.get_books()

        for book in books:
            if book.get("title", "").lower() == title.lower():
                return book

        return None

    def get_highlights_for_book(self, book_id: int) -> list[ReadwiseHighlightInfo]:
        """Get all highlights for a specific book.

        Args:
            book_id: Readwise book ID

        Returns:
            List of ReadwiseHighlightInfo objects
        """
        highlights = []
        next_url: Optional[str] = f"{self.BASE_URL}/highlights/"
        params: dict = {"book_id": book_id}

        while next_url:
            response = self._client.get(next_url, params=params)
            self._check_rate_limit(response)

            if response.status_code != 200:
                raise ReadwiseAPIError(
                    f"Failed to get highlights: {response.status_code} - {response.text}"
                )

            data = response.json()
            highlights.extend(data.get("results", []))
            next_url = data.get("next")
            params = {}

        return highlights


def convert_to_readwise_highlights(
    highlights: list[Highlight],
    document_title: str,
    author: Optional[str] = None,
    source_url: Optional[str] = None,
    category: str = "books",
) -> list[ReadwiseHighlight]:
    """Convert internal Highlight objects to Readwise format.

    Args:
        highlights: List of Highlight objects
        document_title: Title of the source document
        author: Author of the document
        source_url: URL to the source (optional)
        category: Readwise category (books, articles, etc.)

    Returns:
        List of ReadwiseHighlight objects ready for API upload
    """
    readwise_highlights = []

    # Readwise requires a non-blank author
    effective_author = author if author else "Unknown"

    for hl in highlights:
        rw_highlight = ReadwiseHighlight(
            text=hl.text,
            title=document_title,
            author=effective_author,
            source_type="remarkawise",
            category=category,
            location=hl.page_number,
            location_type="page",
            highlighted_at=hl.created_at,
            note=hl.note,
            source_url=source_url,
        )
        readwise_highlights.append(rw_highlight)

    return readwise_highlights
