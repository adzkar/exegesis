"""Typed conversion state threaded through the render pass."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PipelineState:
    """Mutable conversion state carried across pages.

    ``page_items`` / ``page_frags`` are per-page lists (block dicts in,
    HTML fragment strings out); ``par`` / ``par_page`` are the open
    paragraph and the page it began on so a paragraph that crosses a page
    boundary is emitted where it started. ``images`` maps content hash to
    ``epub.EpubItem`` (kept as ``Any`` to avoid an ebooklib import here).
    """

    body_size: float
    line_h: float
    page_items: list[list[dict[str, Any]]]
    edges: list[float]
    drop_edges: bool
    boilerplate: set[str]
    page_frags: list[list[str]]
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
