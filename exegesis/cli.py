"""Command-line entry point."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .emit import convert


def main(argv: list[str] | None = None) -> None:
    """Parse CLI arguments and run the conversion."""
    parser = argparse.ArgumentParser(
        description=(
            "Convert a PDF (including multi-column journal papers) to a "
            "reflowable EPUB: column-aware reading order, heading TOC, "
            "footnotes, and embedded images."
        )
    )
    parser.add_argument("input", type=Path, help="PDF file to convert")
    parser.add_argument("-o", "--output", type=Path, help="output .epub path")
    parser.add_argument("--title", help="override EPUB title")
    parser.add_argument("--author", help="override EPUB author")
    parser.add_argument(
        "--keep-headers",
        action="store_true",
        help="keep header/footer strips (page numbers, running heads)",
    )
    args = parser.parse_args(argv)

    if not args.input.is_file():
        parser.error(f"not a file: {args.input}")
    out = args.output or args.input.with_suffix(".epub")

    stats = convert(
        args.input, out, title=args.title, author=args.author,
        drop_edges=not args.keep_headers,
    )
    print(
        f"{stats['output']}: {stats['pages']} pages -> {stats['chapters']} "
        f"chapter(s), {stats['headings']} heading(s), "
        f"{stats['footnotes']} footnote(s), {stats['images']} image(s)"
    )


if __name__ == "__main__":
    sys.exit(main())
