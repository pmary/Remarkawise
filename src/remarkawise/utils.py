"""Shared utility functions for Remarkawise."""

import hashlib


def generate_highlight_id(document_id: str, page_number: int, text: str) -> str:
    """Generate a deterministic highlight ID.

    Creates a unique ID based on document, page, and text content.
    This ensures the same highlight always gets the same ID.

    Args:
        document_id: The document's unique identifier
        page_number: The page number (1-indexed)
        text: The highlight text content

    Returns:
        A unique highlight ID in format "rm_{hash}"
    """
    # Use first 100 chars of text to avoid very long hashes
    content = f"{document_id}:{page_number}:{text[:100]}"
    hash_value = hashlib.sha256(content.encode()).hexdigest()[:16]
    return f"rm_{hash_value}"


def generate_text_hash(text: str) -> str:
    """Generate a hash of text content for tracking/matching.

    Used for matching highlights between systems (e.g., local to Readwise).

    Args:
        text: The text to hash

    Returns:
        A 32-character hex hash of the text
    """
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def generate_content_checksum(content: str) -> str:
    """Generate a checksum for content comparison.

    Used for detecting changes in highlight collections.

    Args:
        content: The content to checksum

    Returns:
        A 32-character hex checksum
    """
    return hashlib.sha256(content.encode()).hexdigest()[:32]
