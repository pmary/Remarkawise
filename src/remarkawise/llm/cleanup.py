"""LLM-based text cleanup for PDF extraction artifacts.

This module uses Claude to fix corrupted text from PDF extraction,
including ligature issues, missing word boundaries, and broken contractions.
"""


class LLMCleanupError(Exception):
    """Error during LLM text cleanup."""

    pass


class LLMTextCleaner:
    """Uses Claude to clean up corrupted text from PDF extraction."""

    # System prompt for text cleanup
    SYSTEM_PROMPT = (
        "You are a text cleanup assistant. "
        "Your task is to fix corrupted text extracted from PDFs.\n\n"
        "Common issues to fix:\n"
        "- Missing spaces between words (e.g., \"skillsyou've\" → \"skills you've\")\n"
        "- Corrupted ligatures (e.g., \"Sca,olding\" → \"Scaffolding\", "
        "\"di Ierent\" → \"different\")\n"
        "- Broken contractions (e.g., \"that s\" → \"that's\", \"you ve\" → \"you've\")\n"
        "- Random characters from font mapping issues\n\n"
        "Rules:\n"
        "1. ONLY fix obvious corruption - do not rephrase or rewrite\n"
        "2. Preserve the original meaning exactly\n"
        "3. Keep proper nouns, technical terms, and unusual words as-is if intentional\n"
        "4. Return ONLY the cleaned text, nothing else (no explanations)\n"
        "5. If the text looks fine, return it unchanged"
    )

    def __init__(self, api_key: str, model: str = "claude-3-5-haiku-latest") -> None:
        """Initialize the LLM text cleaner.

        Args:
            api_key: Anthropic API key
            model: Model to use (default: claude-3-5-haiku-latest for cost efficiency)
        """
        try:
            import anthropic

            self._anthropic = anthropic  # Store for exception handling
        except ImportError as e:
            raise LLMCleanupError(
                "anthropic package not installed. "
                "Install with: pip install -e '.[llm]'"
            ) from e

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def cleanup_text(self, text: str) -> str:
        """Clean up a single piece of text.

        Args:
            text: Potentially corrupted text from PDF extraction

        Returns:
            Cleaned text
        """
        if not text or not text.strip():
            return text

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=len(text) * 2 + 100,  # Allow some expansion
                system=self.SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": f"Clean this text:\n\n{text}",
                    }
                ],
            )
            return response.content[0].text.strip()
        except self._anthropic.APIError as e:
            raise LLMCleanupError(f"API error during cleanup: {e}") from e
        except (KeyError, IndexError, AttributeError) as e:
            raise LLMCleanupError(f"Invalid response from API: {e}") from e

    def cleanup_batch(self, texts: list[str]) -> list[str]:
        """Clean up multiple texts in a single API call.

        More efficient than calling cleanup_text() multiple times.

        Args:
            texts: List of potentially corrupted texts

        Returns:
            List of cleaned texts (same order as input)
        """
        if not texts:
            return []

        # Filter out empty texts but track their positions
        non_empty: list[tuple[int, str]] = [
            (i, t) for i, t in enumerate(texts) if t and t.strip()
        ]

        if not non_empty:
            return texts

        # Build batch prompt
        batch_prompt = (
            "Clean each numbered text. Return cleaned texts in the same format.\n\n"
        )
        for idx, (_, text) in enumerate(non_empty, 1):
            batch_prompt += f"{idx}. {text}\n"

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=sum(len(t) for _, t in non_empty) * 2 + 500,
                system=self.SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": batch_prompt,
                    }
                ],
            )

            # Parse numbered responses
            response_text = response.content[0].text.strip()
            cleaned_texts = self._parse_numbered_response(response_text, len(non_empty))

            # Reconstruct full list with empty texts in original positions
            result = list(texts)  # Copy original
            for (orig_idx, _), cleaned in zip(non_empty, cleaned_texts, strict=False):
                result[orig_idx] = cleaned

            return result

        except self._anthropic.APIError as e:
            raise LLMCleanupError(f"API error during batch cleanup: {e}") from e
        except (KeyError, IndexError, AttributeError, ValueError) as e:
            raise LLMCleanupError(f"Invalid response from API: {e}") from e

    def _parse_numbered_response(self, response: str, expected_count: int) -> list[str]:
        """Parse numbered response from LLM.

        Args:
            response: LLM response with numbered items
            expected_count: Expected number of items

        Returns:
            List of cleaned texts
        """
        import re

        # Try to parse numbered format: "1. text\n2. text\n..."
        pattern = r"^\d+\.\s*(.+?)(?=\n\d+\.|$)"
        matches = re.findall(pattern, response, re.MULTILINE | re.DOTALL)

        if len(matches) >= expected_count:
            return [m.strip() for m in matches[:expected_count]]

        # Fallback: split by newlines if numbered parsing fails
        lines = [line.strip() for line in response.split("\n") if line.strip()]

        # Remove number prefixes if present
        cleaned_lines = []
        for line in lines:
            # Remove "1. " or "1) " prefix
            cleaned = re.sub(r"^\d+[.)]\s*", "", line)
            if cleaned:
                cleaned_lines.append(cleaned)

        if len(cleaned_lines) >= expected_count:
            return cleaned_lines[:expected_count]

        # If still not enough, return what we have padded with empty strings
        while len(cleaned_lines) < expected_count:
            cleaned_lines.append("")

        return cleaned_lines

    def close(self) -> None:
        """Close the client connection."""
        if hasattr(self._client, "close"):
            self._client.close()


def create_cleaner(api_key: str | None = None) -> "LLMTextCleaner | None":
    """Create an LLM text cleaner if API key is available.

    Args:
        api_key: Anthropic API key (or None to skip)

    Returns:
        LLMTextCleaner instance or None if no API key
    """
    if not api_key:
        return None

    try:
        return LLMTextCleaner(api_key)
    except LLMCleanupError:
        return None
