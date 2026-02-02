"""Parser for reMarkable .rm files containing highlights and annotations.

The .rm file format is a binary format containing strokes/lines drawn on the device.
Highlights are stored as a specific type of stroke with a highlighter tool type.

This module supports both legacy formats (v5 and earlier) and the v6 format
introduced in reMarkable software version 3, using the rmscene library.
"""

import io
import struct
import sys
import warnings
from contextlib import redirect_stderr
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Optional

# Import rmscene for v6 format support
try:
    from rmscene import read_blocks, SceneLineItemBlock, SceneGlyphItemBlock
    from rmscene.scene_items import Pen, PenColor

    RMSCENE_AVAILABLE = True
    HIGHLIGHTER_PENS = {Pen.HIGHLIGHTER_1, Pen.HIGHLIGHTER_2}
except ImportError:
    RMSCENE_AVAILABLE = False
    HIGHLIGHTER_PENS = set()


@dataclass
class GlyphHighlight:
    """A text highlight from a GlyphRange (reMarkable Paper Pro format)."""

    text: str
    start: int
    length: int
    rectangles: list[tuple[float, float, float, float]]  # (x, y, w, h)


@dataclass
class Point:
    """A point in a stroke."""

    x: float
    y: float
    speed: float
    direction: float
    width: float
    pressure: float


@dataclass
class Stroke:
    """A stroke/line drawn on the device."""

    pen_type: int
    color: int
    brush_size: float
    points: list[Point]

    @property
    def is_highlight(self) -> bool:
        """Check if this stroke is a highlighter stroke."""
        # Pen types: 0=brush, 1=pencil, 2=ballpoint, 3=marker, 4=fineliner,
        #            5=highlighter, 6=eraser, 7=sharp pencil, 8=erase area
        # In v6: 5=HIGHLIGHTER_1, 18=HIGHLIGHTER_2
        return self.pen_type in {5, 18}

    @property
    def bounding_box(self) -> tuple[float, float, float, float]:
        """Get bounding box (x_min, y_min, x_max, y_max)."""
        if not self.points:
            return (0, 0, 0, 0)

        x_coords = [p.x for p in self.points]
        y_coords = [p.y for p in self.points]
        return (min(x_coords), min(y_coords), max(x_coords), max(y_coords))


@dataclass
class Page:
    """A page containing strokes."""

    page_number: int
    strokes: list[Stroke]

    @property
    def highlights(self) -> list[Stroke]:
        """Get only highlight strokes from this page."""
        return [s for s in self.strokes if s.is_highlight]


class RMFileParser:
    """Parser for reMarkable .rm files."""

    # reMarkable page dimensions (in device units)
    PAGE_WIDTH = 1404
    PAGE_HEIGHT = 1872

    def __init__(self, file_path: Path, verbose: bool = False) -> None:
        """Initialize parser with path to .rm file."""
        self.file_path = file_path
        self.version: int = 0
        self.verbose = verbose

    def parse(self) -> list[Page]:
        """Parse the .rm file and return pages with strokes.

        Returns:
            List of pages containing strokes
        """
        # Try to detect version from header
        with open(self.file_path, "rb") as f:
            header = f.read(43)
            if header.startswith(b"reMarkable .lines file, version="):
                version_str = header[32:33].decode("ascii")
                try:
                    self.version = int(version_str)
                except ValueError:
                    self.version = 5

        # Use rmscene for v6 format
        if self.version >= 6 and RMSCENE_AVAILABLE:
            return self._parse_v6(verbose=self.verbose)

        # Use legacy parser for older formats
        with open(self.file_path, "rb") as f:
            return self._parse_legacy(f)

    def _parse_v6(self, verbose: bool = False) -> list[Page]:
        """Parse v6 format using rmscene library."""
        strokes: list[Stroke] = []

        def _log(msg: str) -> None:
            if verbose:
                print(f"        [V6] {msg}")

        try:
            # Suppress rmscene warnings about newer format data (printed to stderr)
            stderr_capture = io.StringIO()
            with redirect_stderr(stderr_capture):
                with open(self.file_path, "rb") as f:
                    blocks = list(read_blocks(f))

            _log(f"Read {len(blocks)} blocks from file")

            # Debug: show block types
            block_types: dict[str, int] = {}
            for block in blocks:
                block_type = type(block).__name__
                block_types[block_type] = block_types.get(block_type, 0) + 1
            _log(f"Block types: {block_types}")

            for block in blocks:
                if isinstance(block, SceneLineItemBlock):
                    line = block.item.value
                    if line is None:
                        _log("SceneLineItemBlock has None value, skipping")
                        continue

                    # Check if it has the attributes we need
                    if not hasattr(line, "tool") or not hasattr(line, "points"):
                        _log(f"Line missing tool/points, has: {dir(line)}")
                        continue

                    # Convert rmscene line to our Stroke format
                    points = []
                    for pt in line.points:
                        points.append(
                            Point(
                                x=pt.x,
                                y=pt.y,
                                speed=pt.speed,
                                direction=pt.direction,
                                width=pt.width,
                                pressure=pt.pressure,
                            )
                        )

                    # Map pen type to our format
                    # rmscene uses Pen enum, we need numeric values
                    pen_type = line.tool.value if hasattr(line.tool, "value") else 0

                    # Get color
                    color = line.color.value if hasattr(line.color, "value") else 0

                    _log(f"Found stroke: pen_type={pen_type} ({line.tool}), points={len(points)}")

                    stroke = Stroke(
                        pen_type=pen_type,
                        color=color,
                        brush_size=line.thickness_scale if hasattr(line, "thickness_scale") else 1.0,
                        points=points,
                    )
                    strokes.append(stroke)

        except Exception as e:
            _log(f"Exception during v6 parsing: {e}")
            import traceback
            _log(traceback.format_exc())
            # If rmscene fails, return empty
            return []

        _log(f"Total strokes found: {len(strokes)}")

        # Return as single page (v6 files are per-page)
        if strokes:
            return [Page(page_number=0, strokes=strokes)]
        return []

    def parse_glyph_highlights(self, verbose: bool = False) -> list[GlyphHighlight]:
        """Parse v6 format and extract GlyphRange highlights (reMarkable Paper Pro format).

        These are text highlights where the device has already extracted the text.

        Returns:
            List of GlyphHighlight objects with pre-extracted text
        """
        highlights: list[GlyphHighlight] = []

        def _log(msg: str) -> None:
            if verbose:
                print(f"        [V6-GLYPH] {msg}")

        if not RMSCENE_AVAILABLE:
            _log("rmscene not available")
            return highlights

        try:
            # Suppress rmscene warnings
            stderr_capture = io.StringIO()
            with redirect_stderr(stderr_capture):
                with open(self.file_path, "rb") as f:
                    blocks = list(read_blocks(f))

            _log(f"Read {len(blocks)} blocks from file")

            for block in blocks:
                if isinstance(block, SceneGlyphItemBlock):
                    glyph_range = block.item.value
                    if glyph_range is None:
                        continue

                    # Check if it's a highlight (PenColor.HIGHLIGHT = 9)
                    if hasattr(glyph_range, 'color') and hasattr(glyph_range, 'text'):
                        color_val = glyph_range.color.value if hasattr(glyph_range.color, 'value') else glyph_range.color
                        # PenColor.HIGHLIGHT is 9
                        if color_val == 9 or (hasattr(PenColor, 'HIGHLIGHT') and glyph_range.color == PenColor.HIGHLIGHT):
                            text = glyph_range.text
                            if text:
                                # Extract rectangles
                                rects = []
                                if hasattr(glyph_range, 'rectangles') and glyph_range.rectangles:
                                    for rect in glyph_range.rectangles:
                                        rects.append((rect.x, rect.y, rect.w, rect.h))

                                _log(f"Found glyph highlight: '{text[:50]}...' with {len(rects)} rectangles")

                                highlights.append(GlyphHighlight(
                                    text=text,
                                    start=glyph_range.start if hasattr(glyph_range, 'start') else 0,
                                    length=glyph_range.length if hasattr(glyph_range, 'length') else len(text),
                                    rectangles=rects,
                                ))

        except Exception as e:
            _log(f"Exception during glyph parsing: {e}")
            import traceback
            _log(traceback.format_exc())

        _log(f"Total glyph highlights found (before merge): {len(highlights)}")

        # Merge consecutive highlights (same highlight spanning multiple lines)
        if highlights:
            highlights = self._merge_consecutive_glyph_highlights(highlights, verbose)

        _log(f"Total glyph highlights found (after merge): {len(highlights)}")
        return highlights

    def _merge_consecutive_glyph_highlights(
        self, highlights: list[GlyphHighlight], verbose: bool = False
    ) -> list[GlyphHighlight]:
        """Merge consecutive glyph highlights that are part of the same selection.

        reMarkable stores multi-line highlights as separate GlyphRange objects.
        This merges them back into single highlights.

        Args:
            highlights: List of glyph highlights to merge
            verbose: Enable verbose logging

        Returns:
            Merged list of glyph highlights
        """
        def _log(msg: str) -> None:
            if verbose:
                print(f"        [V6-GLYPH] {msg}")

        if not highlights:
            return highlights

        # Sort by start position
        sorted_highlights = sorted(highlights, key=lambda h: h.start)

        merged: list[GlyphHighlight] = []
        current = sorted_highlights[0]

        for next_hl in sorted_highlights[1:]:
            # Check if highlights are consecutive (allowing small gap for whitespace/newline)
            current_end = current.start + current.length
            gap = next_hl.start - current_end

            # If gap is small (0-5 characters for whitespace/newlines), merge them
            if gap <= 5:
                _log(f"Merging: '{current.text[-20:]}' + '{next_hl.text[:20]}' (gap={gap})")
                # Merge the highlights
                merged_text = current.text + " " + next_hl.text
                merged_rects = current.rectangles + next_hl.rectangles
                current = GlyphHighlight(
                    text=merged_text,
                    start=current.start,
                    length=next_hl.start + next_hl.length - current.start,
                    rectangles=merged_rects,
                )
            else:
                # Not consecutive, save current and start new
                merged.append(current)
                current = next_hl

        # Don't forget the last one
        merged.append(current)

        return merged

    def _parse_legacy(self, f: BinaryIO) -> list[Page]:
        """Parse legacy format (v5 and earlier)."""
        # Read and validate header
        f.seek(0)
        header = f.read(43)
        if not header.startswith(b"reMarkable .lines file, version="):
            raise ValueError(f"Invalid .rm file header: {header[:32]}")

        # Extract version
        version_str = header[32:33].decode("ascii")
        try:
            self.version = int(version_str)
        except ValueError:
            self.version = 5

        # Skip padding to align to known position
        if self.version >= 6:
            f.seek(0x2C)
        else:
            f.seek(43)

        pages: list[Page] = []

        # Read number of pages
        page_count_data = f.read(4)
        if len(page_count_data) < 4:
            return pages

        page_count = struct.unpack("<I", page_count_data)[0]

        for page_idx in range(page_count):
            page = self._parse_page(f, page_idx)
            if page:
                pages.append(page)

        return pages

    def _parse_page(self, f: BinaryIO, page_number: int) -> Optional[Page]:
        """Parse a single page."""
        strokes: list[Stroke] = []

        # Read number of layers
        layer_count_data = f.read(4)
        if len(layer_count_data) < 4:
            return None

        layer_count = struct.unpack("<I", layer_count_data)[0]

        for _ in range(layer_count):
            layer_strokes = self._parse_layer(f)
            strokes.extend(layer_strokes)

        return Page(page_number=page_number, strokes=strokes)

    def _parse_layer(self, f: BinaryIO) -> list[Stroke]:
        """Parse a single layer."""
        strokes: list[Stroke] = []

        # Read number of strokes/lines
        stroke_count_data = f.read(4)
        if len(stroke_count_data) < 4:
            return strokes

        stroke_count = struct.unpack("<I", stroke_count_data)[0]

        for _ in range(stroke_count):
            stroke = self._parse_stroke(f)
            if stroke:
                strokes.append(stroke)

        return strokes

    def _parse_stroke(self, f: BinaryIO) -> Optional[Stroke]:
        """Parse a single stroke/line."""
        # Read stroke header (pen type, color, unknown, brush size, unknown)
        header_data = f.read(4 * 4 + 4)  # 4 ints + 1 float for v5+
        if len(header_data) < 20:
            return None

        pen_type, color, _, brush_size_raw = struct.unpack("<iiiI", header_data[:16])
        brush_size = struct.unpack("<f", struct.pack("<I", brush_size_raw))[0]

        # Skip unknown float in newer versions
        if self.version >= 5:
            _ = f.read(4)

        # Read number of points
        point_count_data = f.read(4)
        if len(point_count_data) < 4:
            return None

        point_count = struct.unpack("<I", point_count_data)[0]

        # Sanity check
        if point_count > 100000:
            return None

        points: list[Point] = []
        for _ in range(point_count):
            point = self._parse_point(f)
            if point:
                points.append(point)

        return Stroke(
            pen_type=pen_type,
            color=color,
            brush_size=brush_size,
            points=points,
        )

    def _parse_point(self, f: BinaryIO) -> Optional[Point]:
        """Parse a single point."""
        # Points are 6 floats: x, y, speed, direction, width, pressure
        point_data = f.read(6 * 4)
        if len(point_data) < 24:
            return None

        x, y, speed, direction, width, pressure = struct.unpack("<ffffff", point_data)

        return Point(
            x=x,
            y=y,
            speed=speed,
            direction=direction,
            width=width,
            pressure=pressure,
        )


class HighlightParser:
    """High-level parser for extracting highlights from .rm files."""

    # Page dimensions for v6 format (may differ from legacy)
    V6_PAGE_WIDTH = 1404
    V6_PAGE_HEIGHT = 1872

    def __init__(self, verbose: bool = False) -> None:
        """Initialize the highlight parser."""
        self.verbose = verbose

    def _log(self, message: str) -> None:
        """Print a message if verbose mode is enabled."""
        if self.verbose:
            print(f"      [PARSER] {message}")

    def extract_highlight_regions(
        self, rm_file_path: Path
    ) -> dict[int, list[tuple[float, float, float, float]]]:
        """Extract highlight regions from an .rm file.

        Args:
            rm_file_path: Path to the .rm file

        Returns:
            Dictionary mapping page numbers to lists of bounding boxes
            (normalized to 0-1 range relative to page dimensions)
        """
        parser = RMFileParser(rm_file_path, verbose=self.verbose)
        pages = parser.parse()

        self._log(f"Parsed {len(pages)} pages, version={parser.version}")

        highlights: dict[int, list[tuple[float, float, float, float]]] = {}

        # Determine page dimensions based on version
        page_width = self.V6_PAGE_WIDTH if parser.version >= 6 else RMFileParser.PAGE_WIDTH
        page_height = self.V6_PAGE_HEIGHT if parser.version >= 6 else RMFileParser.PAGE_HEIGHT

        for page in pages:
            self._log(f"Page {page.page_number}: {len(page.strokes)} total strokes")

            # Show pen types for debugging
            pen_types = {}
            for stroke in page.strokes:
                pen_types[stroke.pen_type] = pen_types.get(stroke.pen_type, 0) + 1
            self._log(f"  Pen types: {pen_types}")

            page_highlights = page.highlights
            self._log(f"  Highlight strokes: {len(page_highlights)}")

            if not page_highlights:
                continue

            # Convert bounding boxes to normalized coordinates
            boxes = []
            for stroke in page_highlights:
                x_min, y_min, x_max, y_max = stroke.bounding_box
                self._log(f"  Highlight bbox: ({x_min:.1f}, {y_min:.1f}, {x_max:.1f}, {y_max:.1f})")

                # Normalize to 0-1 range
                # Note: v6 coordinates may be negative or outside standard range
                # We need to handle this by clamping or adjusting
                norm_box = (
                    max(0, min(1, x_min / page_width)),
                    max(0, min(1, y_min / page_height)),
                    max(0, min(1, x_max / page_width)),
                    max(0, min(1, y_max / page_height)),
                )
                self._log(f"    Normalized: {norm_box}")
                boxes.append(norm_box)

            if boxes:
                highlights[page.page_number] = boxes

        return highlights

    def extract_glyph_highlights(self, rm_file_path: Path) -> list[GlyphHighlight]:
        """Extract pre-extracted text highlights from an .rm file (Paper Pro format).

        On reMarkable Paper Pro, highlights on PDFs/EPUBs are stored as GlyphRange
        objects that already contain the highlighted text.

        Args:
            rm_file_path: Path to the .rm file

        Returns:
            List of GlyphHighlight objects with pre-extracted text
        """
        parser = RMFileParser(rm_file_path, verbose=self.verbose)

        # Check version first
        with open(rm_file_path, "rb") as f:
            header = f.read(43)
            if header.startswith(b"reMarkable .lines file, version="):
                version_str = header[32:33].decode("ascii")
                try:
                    parser.version = int(version_str)
                except ValueError:
                    parser.version = 5

        self._log(f"Checking for glyph highlights (version={parser.version})")

        if parser.version >= 6:
            highlights = parser.parse_glyph_highlights(verbose=self.verbose)
            self._log(f"Found {len(highlights)} glyph highlights")
            return highlights

        return []

    def get_page_number_from_filename(self, rm_file_path: Path) -> Optional[int]:
        """Extract page number from .rm filename.

        reMarkable names highlight files as <page_uuid>.rm within a directory
        structure. The mapping to page numbers is in the .content file.

        Args:
            rm_file_path: Path to the .rm file

        Returns:
            Page number if determinable, None otherwise
        """
        # The .rm files are named by UUID, need to read .content file for mapping
        # For now, try to parse from directory structure
        stem = rm_file_path.stem

        # Try numeric parsing (some older formats)
        try:
            return int(stem)
        except ValueError:
            pass

        return None
