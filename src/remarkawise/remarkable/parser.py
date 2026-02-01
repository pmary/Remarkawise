"""Parser for reMarkable .rm files containing highlights and annotations.

The .rm file format is a binary format containing strokes/lines drawn on the device.
Highlights are stored as a specific type of stroke with a highlighter tool type.

Format overview (v6):
- Header: "reMarkable .lines file, version=X" + padding
- Pages: Each page contains layers, and each layer contains lines
- Lines: Each line has tool type, color, brush size, and points
"""

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Optional


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
        return self.pen_type == 5

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

    def __init__(self, file_path: Path) -> None:
        """Initialize parser with path to .rm file."""
        self.file_path = file_path
        self.version: int = 0

    def parse(self) -> list[Page]:
        """Parse the .rm file and return pages with strokes.

        Returns:
            List of pages containing strokes
        """
        with open(self.file_path, "rb") as f:
            return self._parse_file(f)

    def _parse_file(self, f: BinaryIO) -> list[Page]:
        """Parse the binary .rm file."""
        # Read and validate header
        header = f.read(43)
        if not header.startswith(b"reMarkable .lines file, version="):
            raise ValueError(f"Invalid .rm file header: {header[:32]}")

        # Extract version
        version_str = header[32:33].decode("ascii")
        try:
            self.version = int(version_str)
        except ValueError:
            # Try reading as longer version string
            self.version = 5  # Default to v5 format

        # Skip padding to align to known position
        # Different versions have different header lengths
        if self.version >= 6:
            f.seek(0x2C)  # Version 6 header end
        else:
            f.seek(43)  # Older versions

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

    def __init__(self) -> None:
        """Initialize the highlight parser."""
        pass

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
        parser = RMFileParser(rm_file_path)
        pages = parser.parse()

        highlights: dict[int, list[tuple[float, float, float, float]]] = {}

        for page in pages:
            page_highlights = page.highlights
            if not page_highlights:
                continue

            # Convert bounding boxes to normalized coordinates
            boxes = []
            for stroke in page_highlights:
                x_min, y_min, x_max, y_max = stroke.bounding_box

                # Normalize to 0-1 range
                norm_box = (
                    x_min / RMFileParser.PAGE_WIDTH,
                    y_min / RMFileParser.PAGE_HEIGHT,
                    x_max / RMFileParser.PAGE_WIDTH,
                    y_max / RMFileParser.PAGE_HEIGHT,
                )
                boxes.append(norm_box)

            if boxes:
                highlights[page.page_number] = boxes

        return highlights

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
