"""Typed conversion state threaded through the render pass."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .lang import EN, LangProfile


@dataclass
class PipelineState:
    """Mutable conversion state carried across pages.

    ``page_items`` / ``page_frags`` are per-page lists (block dicts in,
    HTML fragment strings out); ``par`` / ``par_page`` are the open
    paragraph and the page it began on so a paragraph that crosses a page
    boundary is emitted where it started. ``images`` maps content hash to
    ``epub.EpubItem`` (kept as ``Any`` to avoid an ebooklib import here).
    ``lang`` carries the document's pattern profile — every
    language-bound scan reads it.
    """

    body_size: float
    line_h: float
    page_items: list[list[dict[str, Any]]]
    # the document-wide edge grid (#37's prior/fallback); the grid a
    # page actually orders with is ``page_edges[pi]``
    edges: list[float]
    # physical edge index -> reading-column index (``_reading_columns``):
    # outdent edges fold into the reading column on their right while
    # ``edges`` keeps the physical lookup for geometry
    col_map: list[int]
    # per-page active grids from ``_page_grids`` — the doc grid unless a
    # page's own strong candidate overrode it — plus their per-zone
    # ``_reading_columns`` maps and each page's grid provenance
    # ("page" / "doc" / "doc-weak" / "doc-none"), for the layout
    # survey's reporting
    page_edges: list[list[float]]
    page_col_map: list[list[int]]
    grid_sources: list[str]
    drop_edges: bool
    boilerplate: set[str]
    page_frags: list[list[str]]
    lang: LangProfile = EN
    abstract_done: bool = False
    after_h1: bool = False
    byline_text: str | None = None
    images: dict[str, Any] = field(default_factory=dict)
    headings: list[tuple[int, str, str, int]] = field(default_factory=list)
    footnotes: list[tuple[int | None, str]] = field(default_factory=list)
    par: list[str] = field(default_factory=list)
    item: list[str] = field(default_factory=list)
    par_page: int = 0
    heading_seq: int = 0
    # detected-table counters for the convert() stats dict — grid tables
    # emit real <table> markup, raster tables are region-clipped PNGs
    grid_tables: int = 0
    raster_tables: int = 0
    # validated figure regions rasterized so far — artwork and labels
    # emit as one image instead of the labels leaking into prose
    raster_figures: int = 0
    # aside regions emitted so far (stats dict) plus the <aside>
    # fragments queued while a paragraph is still open — the queue
    # drains into par_page's frags right behind the closing paragraph
    asides: int = 0
    pending_asides: list[str] = field(default_factory=list)
