"""Tests for local cache client, focusing on tag-based author extraction."""

import json
from pathlib import Path

import pytest

from remarkawise.remarkable.local_cache import LocalCacheClient


class TestExtractAuthorFromTags:
    """Tests for _extract_author_from_tags method."""

    @pytest.fixture
    def client(self, temp_dir: Path) -> LocalCacheClient:
        """Create a LocalCacheClient with a temporary cache directory."""
        cache_dir = temp_dir / "remarkable_cache"
        cache_dir.mkdir()
        return LocalCacheClient(cache_path=cache_dir)

    def test_single_author_tag(self, client: LocalCacheClient) -> None:
        """Test extracting a single author from tags."""
        tags = ["readwise", "author:Cal Newport", "book"]
        assert client._extract_author_from_tags(tags) == "Cal Newport"

    def test_multiple_author_tags(self, client: LocalCacheClient) -> None:
        """Test extracting multiple authors from tags."""
        tags = ["author:Alice Smith", "author:Bob Jones"]
        assert client._extract_author_from_tags(tags) == "Alice Smith, Bob Jones"

    def test_no_author_tags(self, client: LocalCacheClient) -> None:
        """Test returns None when no author tags present."""
        tags = ["readwise", "book", "article"]
        assert client._extract_author_from_tags(tags) is None

    def test_empty_tags(self, client: LocalCacheClient) -> None:
        """Test returns None with empty tag list."""
        assert client._extract_author_from_tags([]) is None

    def test_case_insensitive_prefix(self, client: LocalCacheClient) -> None:
        """Test that the author: prefix is case-insensitive."""
        assert client._extract_author_from_tags(["Author:Jane Doe"]) == "Jane Doe"
        assert client._extract_author_from_tags(["AUTHOR:Jane Doe"]) == "Jane Doe"
        assert client._extract_author_from_tags(["AuThOr:Jane Doe"]) == "Jane Doe"

    def test_empty_author_name_ignored(self, client: LocalCacheClient) -> None:
        """Test that author: with no name is ignored."""
        tags = ["author:", "author:  ", "author:Valid Author"]
        assert client._extract_author_from_tags(tags) == "Valid Author"

    def test_all_empty_author_names(self, client: LocalCacheClient) -> None:
        """Test that all empty author: tags returns None."""
        tags = ["author:", "author:  "]
        assert client._extract_author_from_tags(tags) is None

    def test_whitespace_trimmed(self, client: LocalCacheClient) -> None:
        """Test that whitespace is trimmed from author name."""
        tags = ["author:  Cal Newport  "]
        assert client._extract_author_from_tags(tags) == "Cal Newport"

    def test_colon_in_author_name(self, client: LocalCacheClient) -> None:
        """Test that colons in the author name are preserved."""
        tags = ["author:Dr. Smith: PhD"]
        assert client._extract_author_from_tags(tags) == "Dr. Smith: PhD"


class TestReadDocumentTagsFiltering:
    """Tests that author: tags are filtered out from regular tags."""

    @pytest.fixture
    def client(self, temp_dir: Path) -> LocalCacheClient:
        """Create a LocalCacheClient with a temporary cache directory."""
        cache_dir = temp_dir / "remarkable_cache"
        cache_dir.mkdir()
        return LocalCacheClient(cache_path=cache_dir)

    def _write_content_file(
        self, cache_dir: Path, doc_id: str, content: dict
    ) -> None:
        """Write a .content file for a document."""
        content_file = cache_dir / f"{doc_id}.content"
        content_file.write_text(json.dumps(content))

    def test_author_tags_excluded(self, client: LocalCacheClient) -> None:
        """Test that author: tags are filtered out of regular tags."""
        self._write_content_file(
            client.cache_path,
            "doc-001",
            {"tags": ["readwise", "author:Cal Newport", "book"]},
        )
        tags = client._read_document_tags("doc-001")
        assert tags == ["readwise", "book"]

    def test_author_tags_excluded_case_insensitive(
        self, client: LocalCacheClient
    ) -> None:
        """Test that Author: and AUTHOR: tags are also filtered."""
        self._write_content_file(
            client.cache_path,
            "doc-001",
            {"tags": ["readwise", "Author:Someone", "AUTHOR:Another"]},
        )
        tags = client._read_document_tags("doc-001")
        assert tags == ["readwise"]

    def test_author_tags_excluded_from_dict_format(
        self, client: LocalCacheClient
    ) -> None:
        """Test that author: tags in dict format are also filtered."""
        self._write_content_file(
            client.cache_path,
            "doc-001",
            {
                "tags": [
                    {"name": "readwise"},
                    {"name": "author:Cal Newport"},
                    {"name": "article"},
                ]
            },
        )
        tags = client._read_document_tags("doc-001")
        assert tags == ["readwise", "article"]

    def test_no_tags_returns_empty(self, client: LocalCacheClient) -> None:
        """Test that no tags returns empty list."""
        self._write_content_file(client.cache_path, "doc-001", {})
        tags = client._read_document_tags("doc-001")
        assert tags == []

    def test_only_author_tags_returns_empty(
        self, client: LocalCacheClient
    ) -> None:
        """Test that only author: tags results in empty tag list."""
        self._write_content_file(
            client.cache_path,
            "doc-001",
            {"tags": ["author:Cal Newport"]},
        )
        tags = client._read_document_tags("doc-001")
        assert tags == []


class TestReadDocumentAuthorWithTags:
    """Tests for author resolution priority in _read_document_author."""

    @pytest.fixture
    def client(self, temp_dir: Path) -> LocalCacheClient:
        """Create a LocalCacheClient with a temporary cache directory."""
        cache_dir = temp_dir / "remarkable_cache"
        cache_dir.mkdir()
        return LocalCacheClient(cache_path=cache_dir)

    def _write_content_file(
        self, cache_dir: Path, doc_id: str, content: dict
    ) -> None:
        """Write a .content file for a document."""
        content_file = cache_dir / f"{doc_id}.content"
        content_file.write_text(json.dumps(content))

    def test_tag_author_takes_priority(self, client: LocalCacheClient) -> None:
        """Test that tag-based author overrides documentMetadata author."""
        self._write_content_file(
            client.cache_path,
            "doc-001",
            {
                "tags": ["author:Manual Author"],
                "documentMetadata": {"authors": ["Embedded Author"]},
            },
        )
        author = client._read_document_author("doc-001")
        assert author == "Manual Author"

    def test_falls_back_to_metadata_author(
        self, client: LocalCacheClient
    ) -> None:
        """Test fallback to documentMetadata when no author tag."""
        self._write_content_file(
            client.cache_path,
            "doc-001",
            {
                "tags": ["readwise"],
                "documentMetadata": {"authors": ["Metadata Author"]},
            },
        )
        author = client._read_document_author("doc-001")
        assert author == "Metadata Author"

    def test_returns_none_when_no_author(
        self, client: LocalCacheClient
    ) -> None:
        """Test returns None when no author source available."""
        self._write_content_file(
            client.cache_path,
            "doc-001",
            {"tags": ["readwise"], "documentMetadata": {}},
        )
        author = client._read_document_author("doc-001")
        assert author is None

    def test_returns_none_when_no_content_file(
        self, client: LocalCacheClient
    ) -> None:
        """Test returns None when .content file doesn't exist."""
        author = client._read_document_author("nonexistent-doc")
        assert author is None

    def test_empty_author_tag_falls_back_to_metadata(
        self, client: LocalCacheClient
    ) -> None:
        """Test that empty author: tag falls back to metadata."""
        self._write_content_file(
            client.cache_path,
            "doc-001",
            {
                "tags": ["author:"],
                "documentMetadata": {"authors": ["Fallback Author"]},
            },
        )
        author = client._read_document_author("doc-001")
        assert author == "Fallback Author"

    def test_dict_format_tags_with_author(
        self, client: LocalCacheClient
    ) -> None:
        """Test author extraction from dict-format tags."""
        self._write_content_file(
            client.cache_path,
            "doc-001",
            {
                "tags": [
                    {"name": "readwise"},
                    {"name": "author:Dict Author"},
                ],
                "documentMetadata": {"authors": ["Metadata Author"]},
            },
        )
        author = client._read_document_author("doc-001")
        assert author == "Dict Author"

    def test_multiple_metadata_authors(
        self, client: LocalCacheClient
    ) -> None:
        """Test multiple authors from documentMetadata (no tag override)."""
        self._write_content_file(
            client.cache_path,
            "doc-001",
            {
                "tags": [],
                "documentMetadata": {"authors": ["Alice", "Bob"]},
            },
        )
        author = client._read_document_author("doc-001")
        assert author == "Alice, Bob"
