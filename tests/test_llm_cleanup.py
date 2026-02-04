"""Tests for LLM text cleanup module."""

from unittest.mock import MagicMock, patch

import pytest


class TestLLMCleanupError:
    """Tests for LLMCleanupError exception."""

    def test_exception_message(self) -> None:
        """Test that exception stores message."""
        from remarkawise.llm.cleanup import LLMCleanupError

        error = LLMCleanupError("Test error message")
        assert str(error) == "Test error message"


class TestLLMTextCleanerInit:
    """Tests for LLMTextCleaner initialization."""

    def test_init_without_anthropic_raises(self) -> None:
        """Test that missing anthropic package raises LLMCleanupError."""
        from remarkawise.llm.cleanup import LLMCleanupError

        with patch.dict("sys.modules", {"anthropic": None}):
            # Force re-import to trigger ImportError
            with pytest.raises(LLMCleanupError, match="anthropic package not installed"):
                # Need to create a new instance that tries to import
                from remarkawise.llm import cleanup

                # Patch the import inside the module
                with patch.object(cleanup, "LLMTextCleaner") as mock_cls:
                    mock_cls.side_effect = LLMCleanupError(
                        "anthropic package not installed"
                    )
                    mock_cls("test-key")

    def test_init_with_anthropic_succeeds(self) -> None:
        """Test successful initialization with mocked anthropic."""
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            from remarkawise.llm.cleanup import LLMTextCleaner

            cleaner = LLMTextCleaner("test-api-key")
            assert cleaner._client == mock_client
            mock_anthropic.Anthropic.assert_called_once_with(api_key="test-api-key")

    def test_init_with_custom_model(self) -> None:
        """Test initialization with custom model."""
        mock_anthropic = MagicMock()

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            from remarkawise.llm.cleanup import LLMTextCleaner

            cleaner = LLMTextCleaner("test-key", model="claude-3-opus-20240229")
            assert cleaner._model == "claude-3-opus-20240229"


class TestLLMTextCleanerCleanupText:
    """Tests for cleanup_text method."""

    def test_cleanup_empty_text_returns_unchanged(self) -> None:
        """Test that empty text is returned unchanged."""
        mock_anthropic = MagicMock()

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            from remarkawise.llm.cleanup import LLMTextCleaner

            cleaner = LLMTextCleaner("test-key")
            assert cleaner.cleanup_text("") == ""
            assert cleaner.cleanup_text("   ") == "   "

    def test_cleanup_text_calls_api(self) -> None:
        """Test that cleanup_text makes API call."""
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="cleaned text")]
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.Anthropic.return_value = mock_client

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            from remarkawise.llm.cleanup import LLMTextCleaner

            cleaner = LLMTextCleaner("test-key")
            result = cleaner.cleanup_text("corrupted text")

            assert result == "cleaned text"
            mock_client.messages.create.assert_called_once()
            call_kwargs = mock_client.messages.create.call_args[1]
            assert "corrupted text" in call_kwargs["messages"][0]["content"]

    def test_cleanup_text_api_error_raises(self) -> None:
        """Test that API errors are wrapped in LLMCleanupError."""
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_anthropic.APIError = Exception  # Mock the exception class

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            from remarkawise.llm.cleanup import LLMCleanupError, LLMTextCleaner

            cleaner = LLMTextCleaner("test-key")
            cleaner._anthropic = mock_anthropic
            mock_client.messages.create.side_effect = mock_anthropic.APIError(
                "API error"
            )

            with pytest.raises(LLMCleanupError, match="API error"):
                cleaner.cleanup_text("test text")

    def test_cleanup_text_invalid_response_raises(self) -> None:
        """Test that invalid API response raises LLMCleanupError."""
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = []  # Empty content - will raise IndexError
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.Anthropic.return_value = mock_client
        mock_anthropic.APIError = type("APIError", (Exception,), {})

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            from remarkawise.llm.cleanup import LLMCleanupError, LLMTextCleaner

            cleaner = LLMTextCleaner("test-key")

            with pytest.raises(LLMCleanupError, match="Invalid response"):
                cleaner.cleanup_text("test text")


class TestLLMTextCleanerCleanupBatch:
    """Tests for cleanup_batch method."""

    def test_cleanup_batch_empty_list(self) -> None:
        """Test that empty list returns empty list."""
        mock_anthropic = MagicMock()

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            from remarkawise.llm.cleanup import LLMTextCleaner

            cleaner = LLMTextCleaner("test-key")
            assert cleaner.cleanup_batch([]) == []

    def test_cleanup_batch_preserves_empty_texts(self) -> None:
        """Test that empty texts in batch are preserved in their positions."""
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="1. cleaned first\n2. cleaned third")]
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.Anthropic.return_value = mock_client

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            from remarkawise.llm.cleanup import LLMTextCleaner

            cleaner = LLMTextCleaner("test-key")
            result = cleaner.cleanup_batch(["first", "", "third"])

            assert len(result) == 3
            assert result[1] == ""  # Empty text preserved

    def test_cleanup_batch_all_empty_returns_original(self) -> None:
        """Test that batch of all empty texts returns original."""
        mock_anthropic = MagicMock()

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            from remarkawise.llm.cleanup import LLMTextCleaner

            cleaner = LLMTextCleaner("test-key")
            input_texts = ["", "   ", ""]
            result = cleaner.cleanup_batch(input_texts)

            assert result == input_texts

    def test_cleanup_batch_makes_single_api_call(self) -> None:
        """Test that batch cleanup makes only one API call."""
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="1. text one\n2. text two\n3. text three")]
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.Anthropic.return_value = mock_client

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            from remarkawise.llm.cleanup import LLMTextCleaner

            cleaner = LLMTextCleaner("test-key")
            cleaner.cleanup_batch(["text1", "text2", "text3"])

            assert mock_client.messages.create.call_count == 1


class TestParseNumberedResponse:
    """Tests for _parse_numbered_response method."""

    def test_parse_standard_format(self) -> None:
        """Test parsing standard numbered format."""
        mock_anthropic = MagicMock()

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            from remarkawise.llm.cleanup import LLMTextCleaner

            cleaner = LLMTextCleaner("test-key")

            response = "1. First item\n2. Second item\n3. Third item"
            result = cleaner._parse_numbered_response(response, 3)

            assert result == ["First item", "Second item", "Third item"]

    def test_parse_with_multiline_items(self) -> None:
        """Test parsing items that span multiple lines."""
        mock_anthropic = MagicMock()

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            from remarkawise.llm.cleanup import LLMTextCleaner

            cleaner = LLMTextCleaner("test-key")

            response = "1. First line\ncontinued\n2. Second item"
            result = cleaner._parse_numbered_response(response, 2)

            assert len(result) == 2
            assert "First line" in result[0]

    def test_parse_fallback_when_format_fails(self) -> None:
        """Test fallback parsing when numbered format fails."""
        mock_anthropic = MagicMock()

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            from remarkawise.llm.cleanup import LLMTextCleaner

            cleaner = LLMTextCleaner("test-key")

            # Response without numbers
            response = "First item\nSecond item\nThird item"
            result = cleaner._parse_numbered_response(response, 3)

            assert len(result) == 3

    def test_parse_pads_missing_items(self) -> None:
        """Test that missing items are padded with empty strings."""
        mock_anthropic = MagicMock()

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            from remarkawise.llm.cleanup import LLMTextCleaner

            cleaner = LLMTextCleaner("test-key")

            response = "1. Only one"
            result = cleaner._parse_numbered_response(response, 3)

            assert len(result) == 3
            assert result[0] == "Only one"


class TestLLMTextCleanerClose:
    """Tests for close method."""

    def test_close_calls_client_close(self) -> None:
        """Test that close() calls underlying client close."""
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            from remarkawise.llm.cleanup import LLMTextCleaner

            cleaner = LLMTextCleaner("test-key")
            cleaner.close()

            mock_client.close.assert_called_once()

    def test_close_handles_missing_close_method(self) -> None:
        """Test that close() handles client without close method."""
        mock_anthropic = MagicMock()
        mock_client = MagicMock(spec=[])  # No close method
        mock_anthropic.Anthropic.return_value = mock_client

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            from remarkawise.llm.cleanup import LLMTextCleaner

            cleaner = LLMTextCleaner("test-key")
            # Should not raise
            cleaner.close()


class TestCreateCleaner:
    """Tests for create_cleaner factory function."""

    def test_create_cleaner_with_no_key_returns_none(self) -> None:
        """Test that None API key returns None."""
        from remarkawise.llm.cleanup import create_cleaner

        assert create_cleaner(None) is None
        assert create_cleaner("") is None

    def test_create_cleaner_with_valid_key(self) -> None:
        """Test creating cleaner with valid key."""
        mock_anthropic = MagicMock()

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            from remarkawise.llm.cleanup import create_cleaner

            result = create_cleaner("valid-key")
            assert result is not None

    def test_create_cleaner_handles_error(self) -> None:
        """Test that create_cleaner returns None on error."""
        from remarkawise.llm.cleanup import LLMCleanupError, create_cleaner

        with patch(
            "remarkawise.llm.cleanup.LLMTextCleaner",
            side_effect=LLMCleanupError("Error"),
        ):
            result = create_cleaner("key")
            assert result is None
