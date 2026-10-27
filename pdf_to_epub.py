#!/usr/bin/env python3
"""Convert a PDF (including multi-column journal papers) to a reflowable EPUB.

Thin CLI shim — the pipeline lives in the ``exegesis`` package:
``analyze`` (font/column/footnote detection), ``order`` (reading order),
``render`` (block classification to HTML fragments), ``emit`` (chapter
chunking + ebooklib packaging).
"""
import sys

from exegesis.cli import main

if __name__ == "__main__":
    sys.exit(main())
