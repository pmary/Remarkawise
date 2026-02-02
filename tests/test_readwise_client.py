"""Tests for Readwise API client."""

from datetime import datetime

import pytest
import respx

from remarkawise.models import Highlight, ReadwiseHighlight
from remarkawise.readwise.client import (
    ReadwiseAPIError,
    ReadwiseClient,
    ReadwiseRateLimitError,
    convert_to_readwise_highlights,
)

from .conftest import MOCK_READWISE_BOOKS_RESPONSE, MOCK_READWISE_HIGHLIGHTS_RESPONSE


class TestReadwiseClientAuth:
    """Tests for Readwise authentication."""

    @respx.mock
    def test_verify_token_valid(self) -> None:
        """Test verifying a valid token."""
        respx.get("https://readwise.io/api/v2/auth").respond(status_code=204)

        client = ReadwiseClient(access_token="valid-token")
        assert client.verify_token() is True
        client.close()

    @respx.mock
    def test_verify_token_invalid(self) -> None:
        """Test verifying an invalid token."""
        respx.get("https://readwise.io/api/v2/auth").respond(status_code=401)

        client = ReadwiseClient(access_token="invalid-token")
        assert client.verify_token() is False
        client.close()

    @respx.mock
    def test_verify_token_network_error(self) -> None:
        """Test handling network error during verification."""
        respx.get("https://readwise.io/api/v2/auth").mock(
            side_effect=Exception("Network error")
        )

        client = ReadwiseClient(access_token="test-token")
        assert client.verify_token() is False
        client.close()


class TestReadwiseClientHighlights:
    """Tests for highlight operations."""

    @respx.mock
    def test_create_highlights_success(self) -> None:
        """Test creating highlights successfully."""
        respx.post("https://readwise.io/api/v2/highlights/").respond(
            status_code=200, json={"highlights": [{"id": 1}, {"id": 2}]}
        )

        client = ReadwiseClient(access_token="test-token")

        highlights = [
            ReadwiseHighlight(
                text="First highlight",
                title="Test Book",
                author="Test Author",
            ),
            ReadwiseHighlight(
                text="Second highlight",
                title="Test Book",
                author="Test Author",
            ),
        ]

        result = client.create_highlights(highlights)
        assert result["created"] == 2
        client.close()

    @respx.mock
    def test_create_highlights_empty_list(self) -> None:
        """Test creating empty highlights list."""
        client = ReadwiseClient(access_token="test-token")

        result = client.create_highlights([])
        assert result["created"] == 0
        client.close()

    @respx.mock
    def test_create_highlights_rate_limited(self) -> None:
        """Test handling rate limit error."""
        respx.post("https://readwise.io/api/v2/highlights/").respond(
            status_code=429, headers={"Retry-After": "30"}
        )

        client = ReadwiseClient(access_token="test-token")

        highlights = [
            ReadwiseHighlight(text="Test highlight", title="Test Book")
        ]

        with pytest.raises(ReadwiseRateLimitError) as exc_info:
            client.create_highlights(highlights)

        assert exc_info.value.retry_after == 30
        client.close()

    @respx.mock
    def test_create_highlights_api_error(self) -> None:
        """Test handling API error."""
        respx.post("https://readwise.io/api/v2/highlights/").respond(
            status_code=500, text="Internal Server Error"
        )

        client = ReadwiseClient(access_token="test-token")

        highlights = [
            ReadwiseHighlight(text="Test highlight", title="Test Book")
        ]

        with pytest.raises(ReadwiseAPIError) as exc_info:
            client.create_highlights(highlights)

        assert "Failed to create highlights" in str(exc_info.value)
        client.close()

    @respx.mock
    def test_create_highlights_with_optional_fields(self) -> None:
        """Test creating highlights with all optional fields."""
        respx.post("https://readwise.io/api/v2/highlights/").respond(
            status_code=200, json={"highlights": [{"id": 1}]}
        )

        client = ReadwiseClient(access_token="test-token")

        highlights = [
            ReadwiseHighlight(
                text="Highlighted text",
                title="Book Title",
                author="Author Name",
                source_type="remarkawise",
                category="books",
                location=42,
                location_type="page",
                highlighted_at=datetime(2024, 1, 15, 10, 30, 0),
                note="My note about this",
                source_url="https://example.com/book",
            )
        ]

        result = client.create_highlights(highlights)
        assert result["created"] == 1

        # Verify the request payload
        request = respx.calls.last.request
        import json
        payload = json.loads(request.content)
        hl = payload["highlights"][0]

        assert hl["text"] == "Highlighted text"
        assert hl["title"] == "Book Title"
        assert hl["author"] == "Author Name"
        assert hl["location"] == 42
        assert hl["note"] == "My note about this"
        client.close()


class TestReadwiseClientBooks:
    """Tests for book/source operations."""

    @respx.mock
    def test_get_books_success(self) -> None:
        """Test getting books list."""
        respx.get("https://readwise.io/api/v2/books/").respond(
            status_code=200, json=MOCK_READWISE_BOOKS_RESPONSE
        )

        client = ReadwiseClient(access_token="test-token")
        books = client.get_books()

        assert len(books) == 2
        assert books[0]["title"] == "Research Paper"
        assert books[1]["title"] == "Another Book"
        client.close()

    @respx.mock
    def test_get_books_paginated(self) -> None:
        """Test getting books with pagination."""
        # First page
        respx.get("https://readwise.io/api/v2/books/").respond(
            status_code=200,
            json={
                "count": 3,
                "next": "https://readwise.io/api/v2/books/?page=2",
                "results": [{"id": 1, "title": "Book 1"}],
            },
        )

        # Second page
        respx.get("https://readwise.io/api/v2/books/?page=2").respond(
            status_code=200,
            json={
                "count": 3,
                "next": None,
                "results": [{"id": 2, "title": "Book 2"}, {"id": 3, "title": "Book 3"}],
            },
        )

        client = ReadwiseClient(access_token="test-token")
        books = client.get_books()

        assert len(books) == 3
        client.close()

    @respx.mock
    def test_get_books_with_category_filter(self) -> None:
        """Test getting books filtered by category."""
        respx.get("https://readwise.io/api/v2/books/").respond(
            status_code=200,
            json={"count": 1, "next": None, "results": [{"id": 1, "title": "Article", "category": "articles"}]},
        )

        client = ReadwiseClient(access_token="test-token")
        books = client.get_books(category="articles")

        # Verify category was passed as parameter
        request = respx.calls.last.request
        assert "category=articles" in str(request.url)
        client.close()

    @respx.mock
    def test_find_book_by_title_found(self) -> None:
        """Test finding a book by title."""
        respx.get("https://readwise.io/api/v2/books/").respond(
            status_code=200, json=MOCK_READWISE_BOOKS_RESPONSE
        )

        client = ReadwiseClient(access_token="test-token")
        book = client.find_book_by_title("Research Paper")

        assert book is not None
        assert book["id"] == 12345
        client.close()

    @respx.mock
    def test_find_book_by_title_not_found(self) -> None:
        """Test finding a book that doesn't exist."""
        respx.get("https://readwise.io/api/v2/books/").respond(
            status_code=200, json=MOCK_READWISE_BOOKS_RESPONSE
        )

        client = ReadwiseClient(access_token="test-token")
        book = client.find_book_by_title("Nonexistent Book")

        assert book is None
        client.close()

    @respx.mock
    def test_find_book_by_title_case_insensitive(self) -> None:
        """Test that book search is case-insensitive."""
        respx.get("https://readwise.io/api/v2/books/").respond(
            status_code=200, json=MOCK_READWISE_BOOKS_RESPONSE
        )

        client = ReadwiseClient(access_token="test-token")
        book = client.find_book_by_title("RESEARCH PAPER")

        assert book is not None
        assert book["title"] == "Research Paper"
        client.close()


class TestReadwiseClientHighlightsForBook:
    """Tests for getting highlights for a specific book."""

    @respx.mock
    def test_get_highlights_for_book(self) -> None:
        """Test getting highlights for a book."""
        respx.get("https://readwise.io/api/v2/highlights/").respond(
            status_code=200, json=MOCK_READWISE_HIGHLIGHTS_RESPONSE
        )

        client = ReadwiseClient(access_token="test-token")
        highlights = client.get_highlights_for_book(book_id=12345)

        assert len(highlights) == 2
        assert highlights[0]["text"] == "Important insight from the paper"
        assert highlights[1]["note"] == "Review later"
        client.close()

    @respx.mock
    def test_get_highlights_rate_limited(self) -> None:
        """Test handling rate limit when getting highlights."""
        respx.get("https://readwise.io/api/v2/highlights/").respond(
            status_code=429, headers={"Retry-After": "60"}
        )

        client = ReadwiseClient(access_token="test-token")

        with pytest.raises(ReadwiseRateLimitError) as exc_info:
            client.get_highlights_for_book(book_id=12345)

        assert exc_info.value.retry_after == 60
        client.close()


class TestConvertToReadwiseHighlights:
    """Tests for highlight conversion function."""

    def test_convert_basic_highlights(self, sample_highlights: list[Highlight]) -> None:
        """Test converting basic highlights."""
        result = convert_to_readwise_highlights(
            sample_highlights,
            document_title="Test Document",
            author="Test Author",
        )

        assert len(result) == 3

        # Check first highlight
        assert result[0].text == "This is the first highlighted passage from the document."
        assert result[0].title == "Test Document"
        assert result[0].author == "Test Author"
        assert result[0].location == 1
        assert result[0].source_type == "remarkawise"
        assert result[0].category == "books"

    def test_convert_highlight_with_note(self, sample_highlights: list[Highlight]) -> None:
        """Test that notes are preserved during conversion."""
        result = convert_to_readwise_highlights(
            sample_highlights,
            document_title="Test Document",
        )

        # Third highlight has a note
        assert result[2].note == "Remember this for the exam"

    def test_convert_with_source_url(self, sample_highlights: list[Highlight]) -> None:
        """Test conversion with source URL."""
        result = convert_to_readwise_highlights(
            sample_highlights,
            document_title="Test Document",
            source_url="https://example.com/doc/123",
        )

        for hl in result:
            assert hl.source_url == "https://example.com/doc/123"

    def test_convert_empty_list(self) -> None:
        """Test converting empty highlight list."""
        result = convert_to_readwise_highlights(
            [],
            document_title="Test Document",
        )

        assert result == []


class TestReadwiseClientDeleteHighlights:
    """Tests for highlight deletion operations."""

    @respx.mock
    def test_delete_highlight_success(self) -> None:
        """Test deleting a highlight successfully."""
        respx.delete("https://readwise.io/api/v2/highlights/12345").respond(
            status_code=204
        )

        client = ReadwiseClient(access_token="test-token")
        result = client.delete_highlight(12345)

        assert result is True
        client.close()

    @respx.mock
    def test_delete_highlight_not_found(self) -> None:
        """Test deleting a highlight that doesn't exist."""
        respx.delete("https://readwise.io/api/v2/highlights/99999").respond(
            status_code=404
        )

        client = ReadwiseClient(access_token="test-token")
        result = client.delete_highlight(99999)

        assert result is False
        client.close()

    @respx.mock
    def test_delete_highlight_rate_limited(self) -> None:
        """Test handling rate limit when deleting."""
        respx.delete("https://readwise.io/api/v2/highlights/12345").respond(
            status_code=429, headers={"Retry-After": "30"}
        )

        client = ReadwiseClient(access_token="test-token")

        with pytest.raises(ReadwiseRateLimitError) as exc_info:
            client.delete_highlight(12345)

        assert exc_info.value.retry_after == 30
        client.close()

    @respx.mock
    def test_delete_highlight_api_error(self) -> None:
        """Test handling API error during deletion."""
        respx.delete("https://readwise.io/api/v2/highlights/12345").respond(
            status_code=500, text="Internal Server Error"
        )

        client = ReadwiseClient(access_token="test-token")

        with pytest.raises(ReadwiseAPIError) as exc_info:
            client.delete_highlight(12345)

        assert "Failed to delete highlight" in str(exc_info.value)
        client.close()

    @respx.mock
    def test_delete_highlights_multiple(self) -> None:
        """Test deleting multiple highlights."""
        respx.delete("https://readwise.io/api/v2/highlights/1").respond(status_code=204)
        respx.delete("https://readwise.io/api/v2/highlights/2").respond(status_code=204)
        respx.delete("https://readwise.io/api/v2/highlights/3").respond(status_code=404)

        client = ReadwiseClient(access_token="test-token")
        deleted = client.delete_highlights([1, 2, 3])

        assert deleted == 2  # Only 2 were actually deleted
        client.close()

    @respx.mock
    def test_create_highlights_returns_readwise_ids(self) -> None:
        """Test that create_highlights returns Readwise IDs."""
        respx.post("https://readwise.io/api/v2/highlights/").respond(
            status_code=200,
            json=[
                {"id": 1001, "text": "First highlight"},
                {"id": 1002, "text": "Second highlight"},
            ]
        )

        client = ReadwiseClient(access_token="test-token")

        highlights = [
            ReadwiseHighlight(
                text="First highlight",
                title="Test Book",
                author="Test Author",
            ),
            ReadwiseHighlight(
                text="Second highlight",
                title="Test Book",
                author="Test Author",
            ),
        ]

        result = client.create_highlights(highlights)

        assert result["created"] == 2
        assert "readwise_ids" in result
        # The readwise_ids should map text_hash to Readwise ID
        assert len(result["readwise_ids"]) == 2
        client.close()


class TestReadwiseClientContextManager:
    """Tests for context manager usage."""

    def test_context_manager(self) -> None:
        """Test using client as context manager."""
        with ReadwiseClient(access_token="test-token") as client:
            assert client.access_token == "test-token"
            assert client._client is not None

    def test_close_cleans_up(self) -> None:
        """Test that close() cleans up resources."""
        client = ReadwiseClient(access_token="test-token")
        http_client = client._client

        client.close()

        assert http_client.is_closed
