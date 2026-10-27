"""exegesis — PDF to reflowable EPUB for multi-column academic papers.

Rule-based layout analysis over PyMuPDF blocks: column-aware reading
order, font/position heuristics for headings/bylines/footnotes, image and
rotated-table extraction, then ebooklib packaging.
"""
from .emit import convert

__all__ = ["convert"]
