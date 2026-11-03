"""exegesis — PDF to reflowable EPUB for multi-column academic papers.

Rule-based layout analysis over PyMuPDF blocks: column-aware reading
order, font/position heuristics for headings/bylines/footnotes, image and
rotated-table extraction, then ebooklib packaging.
"""
__version__ = "0.1.2"

from .emit import convert
from .lang import LangProfile, get_lang

__all__ = ["LangProfile", "convert", "get_lang"]
