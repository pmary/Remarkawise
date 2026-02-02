"""PDF highlight extraction using PyMuPDF (fitz).

This module extracts text from PDF regions that correspond to highlight
annotations from reMarkable devices.
"""

import hashlib
import re
from pathlib import Path
from typing import Optional
from uuid import uuid4

import fitz  # PyMuPDF

from remarkawise.models import Highlight

# PDF ligature mappings (Unicode ligatures to their decomposed forms)
LIGATURE_MAP = {
    "\ufb00": "ff",   # ﬀ
    "\ufb01": "fi",   # ﬁ
    "\ufb02": "fl",   # ﬂ
    "\ufb03": "ffi",  # ﬃ
    "\ufb04": "ffl",  # ﬄ
    "\ufb05": "st",   # ﬅ (long s + t)
    "\ufb06": "st",   # ﬆ
}

# Corrupted ligature patterns from PDFs with broken font mappings
# These appear when fonts lack proper ToUnicode CMap entries
# The pattern is: corrupted_char -> (likely_ligature, context_pattern)
# We use regex to only replace when the character appears within a word

# Common corrupted ligature mappings (vary by PDF font)
# Pattern: character that appears in middle of words where ligature would be
CORRUPTED_LIGATURE_PATTERNS = [
    # (pattern, replacement) - pattern matches corrupted char surrounded by letters
    (r'(?<=[a-zA-Z])\+(?=[a-zA-Z])', 'fi'),   # + in word = fi (e.g., "ef+cient" → "efficient")
    (r'(?<=[a-zA-Z]),(?=[a-zA-Z])', 'ff'),    # , in word = ff (e.g., "sca,olding" → "scaffolding")
    (r'(?<=[a-zA-Z])\)(?=[a-zA-Z])', 'fl'),   # ) in word = fl (e.g., "work)ows" → "workflows")
    (r'(?<=[a-zA-Z])\((?=[a-zA-Z])', 'ffi'),  # ( in word = ffi
    (r'(?<=[a-zA-Z])\*(?=[a-zA-Z])', 'ffl'),  # * in word = ffl
    (r'(?<=[a-z])I(?=[a-z])', 'ff'),          # I between lowercase = ff (e.g., "diIerent" → "different")
]

# Also handle start-of-word ligatures (common with fi/fl)
START_WORD_PATTERNS = [
    (r'(?<![a-zA-Z])\+(?=[a-z])', 'fi'),  # +nancial → financial, +le → file
    (r'(?<![a-zA-Z])\)(?=[a-z])', 'fl'),  # )ow → flow, )oor → floor
]

# Word boundary patterns for fixing missing spaces from PDF line breaks
# reMarkable's GlyphRange extraction concatenates words at line breaks
#
# We use a conservative approach: only insert spaces in specific patterns
# that are very likely to be concatenated words, not legitimate compound words.
#
# Pattern: (word ending) + (common word starter)
# - Word endings: s (plurals), ed (past tense), ly (adverbs), ing, er, etc.
# - Word starters: the, a, an, to, for, and, or, is, are, etc.

# Safe patterns: word endings that rarely continue into another syllable
WORD_BOUNDARY_PATTERNS = [
    # Plurals/verbs ending in 's' followed by common words
    # Use word boundary or end-of-string to handle spaces after
    (r'([a-z]+s)(the)(?=[a-z]|\s|$)', r'\1 \2'),      # "modelsthe" → "models the"
    (r'([a-z]+s)(a)(?=[bcdfghjklmnpqrstvwxyz])', r'\1 \2'),  # "modelsa..." but not "modelsa" + vowel
    (r'([a-z]+s)(to)(?=[a-z]|\s|$)', r'\1 \2'),       # "needsto" → "needs to"
    (r'([a-z]+s)(for)(?=[a-z]|\s|$)', r'\1 \2'),      # "modelsfor" → "models for"
    (r'([a-z]+s)(and)(?=[a-z]|\s|$)', r'\1 \2'),      # "modelsand" → "models and"
    (r'([a-z]+s)(or)(?=[a-z]|\s|$)', r'\1 \2'),       # "modelsor" → "models or"
    (r'([a-z]+s)(is)(?=[a-z]|\s|$)', r'\1 \2'),       # "thisis" → "this is"
    (r'([a-z]+s)(are)(?=[a-z]|\s|$)', r'\1 \2'),      # "modelsare" → "models are"
    (r'([a-z]+s)(will)(?=[a-z]|\s|$)', r'\1 \2'),     # "modelswill" → "models will"
    (r'([a-z]+s)(you)(?=[a-z\']|\s|$)', r'\1 \2'),    # "skillsyou" → "skills you" (include apostrophe)
    (r'([a-z]+s)(your)(?=[a-z]|\s|$)', r'\1 \2'),     # "makesyour" → "makes your"
    (r'([a-z]+s)(we)(?=[a-z]|\s|$)', r'\1 \2'),       # "letswe" → "lets we"
    (r'([a-z]+s)(that)(?=[a-z]|\s|$)', r'\1 \2'),     # "meansthat" → "means that"
    (r'([a-z]+s)(how)(?=[a-z]|\s|$)', r'\1 \2'),      # "knowshow" → "knows how"
    (r'([a-z]+s)(what)(?=[a-z]|\s|$)', r'\1 \2'),     # "knowswhat" → "knows what"
    (r'([a-z]+s)(not)(?=[a-z]|\s|$)', r'\1 \2'),      # "doesnot" → "does not"
    (r'([a-z]+s)(need)(?=[a-z]|\s|$)', r'\1 \2'),     # "modelsneed" → "models need"
    (r'([a-z]+s)(have)(?=[a-z]|\s|$)', r'\1 \2'),     # "modelshave" → "models have"
    (r'([a-z]+s)(can)(?=[a-z]|\s|$)', r'\1 \2'),      # "modelscan" → "models can"
    (r'([a-z]+s)(with)(?=[a-z]|\s|$)', r'\1 \2'),     # "modelswith" → "models with"
    (r'([a-z]+s)(as)(?=[bcdfghjklmnpqrstvwxyz])', r'\1 \2'),  # "modelsas..." but not before vowel
    (r'([a-z]+s)(less)(?=[a-z]|\s|$)', r'\1 \2'),     # "needsless" → "needs less"

    # Past tense 'ed' followed by common words
    (r'([a-z]+ed)(the)(?=[a-z]|\s|$)', r'\1 \2'),     # "neededthe" → "needed the"
    (r'([a-z]+ed)(a)(?=[bcdfghjklmnpqrstvwxyz])', r'\1 \2'),
    (r'([a-z]+ed)(to)(?=[a-z]|\s|$)', r'\1 \2'),
    (r'([a-z]+ed)(and)(?=[a-z]|\s|$)', r'\1 \2'),

    # Adverbs 'ly' followed by common words
    (r'([a-z]+ly)(the)(?=[a-z]|\s|$)', r'\1 \2'),     # "franklythe" → "frankly the"
    (r'([a-z]+ly)(how)(?=[a-z]|\s|$)', r'\1 \2'),     # "franklyhow" → "frankly how"
    (r'([a-z]+ly)(you)(?=[a-z]|\s|$)', r'\1 \2'),

    # Words ending in 'e' (common) followed by specific words
    (r'([a-z]+re)(the)(?=[a-z]|\s|$)', r'\1 \2'),     # "arethe" → "are the"
    (r'([a-z]+re)(essential)(?=[a-z]|\s|$|[,.])', r'\1 \2'),  # "areessential" → "are essential"
    (r'([a-z]+le)(the)(?=[a-z]|\s|$)', r'\1 \2'),     # "whilethe" → "while the"
    (r'([a-z]+me)(you)(?=[a-z]|\s|$)', r'\1 \2'),     # "timeyou" → "time you"

    # 'as' at word boundary (careful - "as" is common inside words)
    (r'([a-z]+s)(as)(?=[bcdfghjklmnpqrstvwxyz][a-z])', r'\1 \2'),  # "modelsasmodels" → "models as models"

    # 'and' patterns
    (r'([a-z]+d)(and)(?=[a-z]|\s|$)', r'\1 \2'),      # "andand" → "and and"
    (r'([a-z]+nd)(frankly)(?=[a-z]|\s|$)', r'\1 \2'),  # "andfrankly" → "and frankly"

    # Specific common patterns seen in the document
    (r'(the)(product)(?=[a-z]|\s|$|[,.])', r'\1 \2'),  # "theproduct" → "the product"
    (r'(as)(models)(?=[a-z]|\s|$)', r'\1 \2'),         # "asmodels" → "as models"
    (r'(agents)(reliable)(?=[a-z]|\s|$)', r'\1 \2'),   # "agentsreliable" → "agents reliable"
]


def fix_missing_word_boundaries(text: str) -> str:
    """Fix missing spaces between words caused by PDF line break extraction.

    reMarkable's GlyphRange extraction often concatenates words when they
    span line breaks in the PDF (e.g., "modelswill" → "models will").

    Uses conservative patterns to avoid breaking legitimate compound words.

    Args:
        text: Text potentially missing word boundary spaces

    Returns:
        Text with word boundaries restored
    """
    for pattern, replacement in WORD_BOUNDARY_PATTERNS:
        text = re.sub(pattern, replacement, text)

    return text


def decode_ligatures(text: str) -> str:
    """Decode PDF ligatures and fix corrupted ligature characters.

    PDFs often use typographic ligatures (fi, fl, ff, etc.) as single
    Unicode characters. Some PDFs with broken font mappings extract
    ligatures as wrong ASCII characters (e.g., "fi" as "+").

    This function handles both cases:
    1. Unicode ligatures (U+FB00-U+FB06) → component letters
    2. Corrupted ASCII chars in word context → likely ligatures

    Args:
        text: Text potentially containing ligature characters

    Returns:
        Text with ligatures replaced by their component letters
    """
    # First, handle proper Unicode ligatures
    for ligature, replacement in LIGATURE_MAP.items():
        text = text.replace(ligature, replacement)

    # Then, fix corrupted ligature patterns (ASCII chars that should be ligatures)
    # Only replace when the character appears within a word context
    for pattern, replacement in CORRUPTED_LIGATURE_PATTERNS:
        text = re.sub(pattern, replacement, text)

    for pattern, replacement in START_WORD_PATTERNS:
        text = re.sub(pattern, replacement, text)

    # Finally, fix missing word boundaries from PDF line breaks
    text = fix_missing_word_boundaries(text)

    return text


class PDFHighlightExtractor:
    """Extracts highlighted text from PDFs using coordinate regions."""

    def __init__(self, pdf_path: Path) -> None:
        """Initialize with a PDF file path.

        Args:
            pdf_path: Path to the PDF file
        """
        self.pdf_path = pdf_path
        self._doc: Optional[fitz.Document] = None

    def __enter__(self) -> "PDFHighlightExtractor":
        self._doc = fitz.open(self.pdf_path)
        return self

    def __exit__(self, *args: object) -> None:
        if self._doc:
            self._doc.close()

    @property
    def doc(self) -> fitz.Document:
        """Get the PDF document, opening it if necessary."""
        if self._doc is None:
            self._doc = fitz.open(self.pdf_path)
        return self._doc

    @property
    def page_count(self) -> int:
        """Get the number of pages in the PDF."""
        return len(self.doc)

    def extract_text_from_region(
        self,
        page_number: int,
        region: tuple[float, float, float, float],
    ) -> str:
        """Extract text from a normalized region on a page.

        Args:
            page_number: 0-indexed page number
            region: Normalized bounding box (x0, y0, x1, y1) with values 0-1

        Returns:
            Extracted text from the region
        """
        if page_number < 0 or page_number >= len(self.doc):
            return ""

        page = self.doc[page_number]
        page_rect = page.rect

        # Convert normalized coordinates to page coordinates
        x0 = region[0] * page_rect.width
        y0 = region[1] * page_rect.height
        x1 = region[2] * page_rect.width
        y1 = region[3] * page_rect.height

        # Create a rectangle for text extraction
        rect = fitz.Rect(x0, y0, x1, y1)

        # Extract text from the rectangle
        text = page.get_text("text", clip=rect)

        # Decode PDF ligatures (fi, fl, ff, etc.)
        text = decode_ligatures(text)

        return text.strip()

    def extract_highlights_from_regions(
        self,
        document_id: str,
        highlight_regions: dict[int, list[tuple[float, float, float, float]]],
    ) -> list[Highlight]:
        """Extract highlight text from multiple regions.

        Args:
            document_id: ID of the source document
            highlight_regions: Dictionary mapping page numbers to lists of
                             normalized bounding boxes

        Returns:
            List of Highlight objects with extracted text
        """
        highlights: list[Highlight] = []

        for page_num, regions in highlight_regions.items():
            for region in regions:
                text = self.extract_text_from_region(page_num, region)

                if not text:
                    continue

                # Generate a deterministic ID based on content
                highlight_id = self._generate_highlight_id(document_id, page_num, text)

                # Calculate position as fraction of page height
                position = (region[1] + region[3]) / 2  # Average of y coordinates

                highlight = Highlight(
                    id=highlight_id,
                    document_id=document_id,
                    text=text,
                    page_number=page_num + 1,  # Convert to 1-indexed
                    position=position,
                )
                highlights.append(highlight)

        return highlights

    def extract_native_highlights(self, document_id: str) -> list[Highlight]:
        """Extract highlights that are natively embedded in the PDF.

        Some PDFs have highlight annotations added by other PDF readers.
        This extracts those in addition to reMarkable highlights.

        Args:
            document_id: ID of the source document

        Returns:
            List of Highlight objects
        """
        highlights: list[Highlight] = []

        for page_num in range(len(self.doc)):
            page = self.doc[page_num]

            # Get all annotations on the page
            for annot in page.annots() or []:
                # Check if it's a highlight annotation (type 8)
                if annot.type[0] != 8:
                    continue

                # Get the highlighted text
                rect = annot.rect
                text = page.get_text("text", clip=rect).strip()

                # Decode PDF ligatures (fi, fl, ff, etc.)
                text = decode_ligatures(text)

                if not text:
                    continue

                highlight_id = self._generate_highlight_id(document_id, page_num, text)

                # Get any associated comment/note
                note = annot.info.get("content", "")

                highlight = Highlight(
                    id=highlight_id,
                    document_id=document_id,
                    text=text,
                    page_number=page_num + 1,
                    position=rect.y0 / page.rect.height,
                    note=note if note else None,
                )
                highlights.append(highlight)

        return highlights

    def _generate_highlight_id(
        self, document_id: str, page_number: int, text: str
    ) -> str:
        """Generate a deterministic ID for a highlight.

        This ensures the same highlight gets the same ID across syncs.
        """
        content = f"{document_id}:{page_number}:{text[:100]}"
        hash_value = hashlib.sha256(content.encode()).hexdigest()[:16]
        return f"rm_{hash_value}"

    def get_document_metadata(self) -> dict[str, Optional[str]]:
        """Extract metadata from the PDF.

        Returns:
            Dictionary with title, author, and other metadata.
            Title may be None if not present in PDF metadata.
        """
        metadata = self.doc.metadata

        return {
            "title": metadata.get("title") or None,
            "author": metadata.get("author"),
            "subject": metadata.get("subject"),
            "creator": metadata.get("creator"),
        }


def merge_highlight_regions(
    regions: list[tuple[float, float, float, float]],
    merge_threshold: float = 0.02,
) -> list[tuple[float, float, float, float]]:
    """Merge overlapping or adjacent highlight regions.

    When highlighting multiple lines, reMarkable may create separate
    strokes that should be merged into a single highlight.

    Args:
        regions: List of normalized bounding boxes
        merge_threshold: Maximum gap to merge (as fraction of page)

    Returns:
        Merged list of bounding boxes
    """
    if not regions:
        return []

    # Sort by y-coordinate (top to bottom)
    sorted_regions = sorted(regions, key=lambda r: (r[1], r[0]))

    merged: list[tuple[float, float, float, float]] = []
    current = sorted_regions[0]

    for region in sorted_regions[1:]:
        # Check if regions are vertically adjacent and horizontally overlapping
        vertical_gap = region[1] - current[3]
        horizontal_overlap = min(current[2], region[2]) - max(current[0], region[0])

        if vertical_gap <= merge_threshold and horizontal_overlap > -merge_threshold:
            # Merge the regions
            current = (
                min(current[0], region[0]),
                min(current[1], region[1]),
                max(current[2], region[2]),
                max(current[3], region[3]),
            )
        else:
            merged.append(current)
            current = region

    merged.append(current)
    return merged
