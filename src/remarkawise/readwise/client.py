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
    ) -> dict:
        """Create multiple highlights in Readwise.

        Args:
            highlights: List of highlights to create

        Returns:
            Dictionary with:
                - created: count of created highlights
                - readwise_ids: mapping of text_hash to Readwise highlight ID

        Raises:
            ReadwiseAPIError: If the API request fails
            ReadwiseRateLimitError: If rate limit is exceeded
        """
        if not highlights:
            return {"created": 0, "readwise_ids": {}}

        import hashlib

        # Build mapping of text_hash to highlight for response parsing
        text_hashes = {}
        for h in highlights:
            text_hash = hashlib.sha256(h.text.encode()).hexdigest()[:32]
            text_hashes[h.text] = text_hash

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

        # Parse response to get Readwise IDs
        readwise_ids: dict[str, int] = {}
        try:
            data = response.json()

            # Debug: show response structure
            import os
            if os.environ.get("REMARKAWISE_DEBUG"):
                import sys
                print(f"  [READWISE] Response type: {type(data).__name__}", file=sys.stderr)
                if isinstance(data, dict):
                    print(f"  [READWISE] Response keys: {list(data.keys())}", file=sys.stderr)
                elif isinstance(data, list) and data:
                    print(f"  [READWISE] First item keys: {list(data[0].keys()) if data[0] else 'empty'}", file=sys.stderr)

            # Response format varies:
            # - List of books with modified_highlights: [{"id": book_id, "modified_highlights": [...]}]
            # - List of highlights directly: [{"id": 1, "text": "..."}, ...]
            # - {"modified_highlights": [...]}
            # - {"highlights": [...]}
            created_highlights: list[dict] = []

            # Track book IDs for fallback fetching
            book_ids: list[int] = []

            if isinstance(data, list):
                # Check if it's a list of books (each with modified_highlights)
                # or a list of highlights directly
                if data and isinstance(data[0], dict) and "modified_highlights" in data[0]:
                    # List of books - extract highlights and book IDs
                    for book in data:
                        book_id = book.get("id")
                        if book_id:
                            book_ids.append(book_id)
                        modified = book.get("modified_highlights", [])
                        created_highlights.extend(modified)
                else:
                    # List of highlights directly
                    created_highlights = data
            elif isinstance(data, dict):
                created_highlights = (
                    data.get("modified_highlights")
                    or data.get("highlights")
                    or []
                )

            if os.environ.get("REMARKAWISE_DEBUG") and created_highlights:
                import sys
                print(f"  [READWISE] Found {len(created_highlights)} items in modified_highlights", file=sys.stderr)
                if created_highlights:
                    first = created_highlights[0]
                    if isinstance(first, dict):
                        print(f"  [READWISE] First item keys: {list(first.keys())}", file=sys.stderr)
                    else:
                        print(f"  [READWISE] First item type: {type(first).__name__} (value: {first})", file=sys.stderr)

            # Check if we got full objects or just IDs
            got_ids_only = created_highlights and isinstance(created_highlights[0], int)

            if got_ids_only and book_ids:
                # API returned just highlight IDs - fetch full highlight data from book
                if os.environ.get("REMARKAWISE_DEBUG"):
                    import sys
                    print(f"  [READWISE] Got {len(created_highlights)} highlight IDs, fetching details from book(s)...", file=sys.stderr)

                # Fetch highlights for each book and match by text
                for book_id in book_ids:
                    try:
                        book_highlights = self.get_highlights_for_book(book_id)
                        for rw_hl in book_highlights:
                            text = rw_hl.get("text", "")
                            rw_id = rw_hl.get("id")
                            if text and rw_id:
                                text_hash = text_hashes.get(text)
                                if text_hash:
                                    readwise_ids[text_hash] = rw_id
                    except Exception as e:
                        if os.environ.get("REMARKAWISE_DEBUG"):
                            import sys
                            print(f"  [READWISE] Failed to fetch highlights for book {book_id}: {e}", file=sys.stderr)
            else:
                # Got full highlight objects - extract directly
                for rw_hl in created_highlights:
                    if isinstance(rw_hl, dict):
                        text = rw_hl.get("text", "")
                        rw_id = rw_hl.get("id")
                        if text and rw_id:
                            text_hash = text_hashes.get(text)
                            if text_hash:
                                readwise_ids[text_hash] = rw_id
        except (ValueError, KeyError) as e:
            # If we can't parse the response, continue without IDs
            import sys
            print(f"  [DEBUG] Failed to parse Readwise response: {e}", file=sys.stderr)
            print(f"  [DEBUG] Response: {response.text[:500]}", file=sys.stderr)

        return {"created": len(highlights), "readwise_ids": readwise_ids}

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
        response = self._client.delete(
            f"{self.BASE_URL}/highlights/{highlight_id}",
        )

        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 60))
            raise ReadwiseRateLimitError(retry_after)

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

    # Readwise requires a non-blank author
    effective_author = author if author else "Unknown"

    for hl in highlights:
        rw_highlight = ReadwiseHighlight(
            text=hl.text,
            title=document_title,
            author=effective_author,
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
