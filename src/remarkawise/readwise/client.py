"""Readwise Reader API client.

Readwise provides an API for importing highlights from various sources.
Documentation: https://readwise.io/api_deets
"""

from datetime import datetime
from typing import Optional

import httpx

from remarkawise.models import Highlight, ReadwiseHighlight


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

    def create_highlights(
        self,
        highlights: list[ReadwiseHighlight],
    ) -> dict[str, int]:
        """Create multiple highlights in Readwise.

        Args:
            highlights: List of highlights to create

        Returns:
            Dictionary with count of created highlights

        Raises:
            ReadwiseAPIError: If the API request fails
            ReadwiseRateLimitError: If rate limit is exceeded
        """
        if not highlights:
            return {"created": 0}

        # Format highlights for the API
        payload = {
            "highlights": [
                {
                    "text": h.text,
                    "title": h.title,
                    "author": h.author,
                    "source_type": h.source_type,
                    "category": h.category,
                    "location": h.location,
                    "location_type": h.location_type,
                    "highlighted_at": (
                        h.highlighted_at.isoformat() if h.highlighted_at else None
                    ),
                    "note": h.note,
                    "source_url": h.source_url,
                }
                for h in highlights
            ]
        }

        # Remove None values
        for hl in payload["highlights"]:
            for key in list(hl.keys()):
                if hl[key] is None:
                    del hl[key]

        response = self._client.post(
            f"{self.BASE_URL}/highlights/",
            json=payload,
        )

        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 60))
            raise ReadwiseRateLimitError(retry_after)

        if response.status_code not in (200, 201):
            raise ReadwiseAPIError(
                f"Failed to create highlights: {response.status_code} - {response.text}"
            )

        return {"created": len(highlights)}

    def get_books(self, category: Optional[str] = None) -> list[dict]:
        """Get list of books/sources in Readwise.

        Args:
            category: Filter by category (books, articles, tweets, etc.)

        Returns:
            List of book/source dictionaries
        """
        params = {}
        if category:
            params["category"] = category

        books = []
        next_url: Optional[str] = f"{self.BASE_URL}/books/"

        while next_url:
            response = self._client.get(next_url, params=params)

            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 60))
                raise ReadwiseRateLimitError(retry_after)

            if response.status_code != 200:
                raise ReadwiseAPIError(
                    f"Failed to get books: {response.status_code} - {response.text}"
                )

            data = response.json()
            books.extend(data.get("results", []))
            next_url = data.get("next")
            params = {}  # Clear params for pagination URL

        return books

    def find_book_by_title(self, title: str) -> Optional[dict]:
        """Find a book by its title.

        Args:
            title: Book title to search for

        Returns:
            Book dictionary if found, None otherwise
        """
        books = self.get_books()

        for book in books:
            if book.get("title", "").lower() == title.lower():
                return book

        return None

    def get_highlights_for_book(self, book_id: int) -> list[dict]:
        """Get all highlights for a specific book.

        Args:
            book_id: Readwise book ID

        Returns:
            List of highlight dictionaries
        """
        highlights = []
        next_url: Optional[str] = f"{self.BASE_URL}/highlights/"
        params = {"book_id": book_id}

        while next_url:
            response = self._client.get(next_url, params=params)

            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 60))
                raise ReadwiseRateLimitError(retry_after)

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
) -> list[ReadwiseHighlight]:
    """Convert internal Highlight objects to Readwise format.

    Args:
        highlights: List of Highlight objects
        document_title: Title of the source document
        author: Author of the document
        source_url: URL to the source (optional)

    Returns:
        List of ReadwiseHighlight objects ready for API upload
    """
    readwise_highlights = []

    for hl in highlights:
        rw_highlight = ReadwiseHighlight(
            text=hl.text,
            title=document_title,
            author=author,
            source_type="remarkawise",
            category="books",
            location=hl.page_number,
            location_type="page",
            highlighted_at=hl.created_at,
            note=hl.note,
            source_url=source_url,
        )
        readwise_highlights.append(rw_highlight)

    return readwise_highlights
