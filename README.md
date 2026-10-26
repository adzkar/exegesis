# exegesis

> *exegesis* — Greek ἐξήγησις, "to lead out." This tool leads the text out of
> its fixed layout.

Convert PDFs — especially **two-column academic papers and journals** — into
clean, reflowable EPUB ebooks for e-readers. Pure Python, rule-based layout
analysis, no OCR or ML dependencies.

## Features

- **Two-column reading order** — text blocks are segmented by the page midline
  and emitted left-column-then-right, so papers read correctly instead of
  interleaving lines.
- **Paragraph reconstruction** — merges mid-sentence splits across blocks,
  columns, and page breaks; indented or post-sentence blocks start new
  paragraphs.
- **Table of contents & chapters** — headings detected by font analysis
  become a navigable TOC, and chapters split exactly at each heading.
- **Footnote extraction** — numbered footnotes *and* unnumbered author notes
  (below a rule/gap) move to a linked **Notes** chapter; in-body superscripts
  become clickable references.
- **Header/footer stripping** — running heads and page numbers detected by
  repetition and removed.
- **Front-matter handling** — title, byline/affiliation, and abstract are
  detected and kept as distinct, well-structured elements.
- **Image extraction** — embedded figures are deduplicated and included.

## Requirements

```bash
pip3 install -r requirements.txt   # pymupdf, ebooklib
```

## Usage

```bash
python3 pdf_to_epub.py paper.pdf                      # -> paper.epub
python3 pdf_to_epub.py paper.pdf -o out.epub
python3 pdf_to_epub.py paper.pdf --title "My Paper" --author "J. Doe"
python3 pdf_to_epub.py paper.pdf --keep-headers       # keep headers/footers
```

## How it works

1. **Extract** — PyMuPDF yields positioned text/image blocks per page.
2. **Order** — full-width blocks split the page into horizontal segments;
   each segment reads left column then right.
3. **Render** — blocks become headings, bylines, paragraphs, footnotes, or
   figures based on font size, indentation, position, and repetition.
4. **Emit** — fragments are chunked into chapters at headings, footnotes are
   collected into a Notes section, and everything is packaged via ebooklib.

See the [pipeline diagram](docs/pipeline.md) for a visual walkthrough and
[AGENTS.md](AGENTS.md) for the detailed heuristics.

## Limitations

- **No OCR.** Scanned PDFs without a text layer produce image-only output —
  an OCR pass (e.g. tesseract) would be needed as a separate step.
- Complex layouts (dense figures, equations, tables) may read imperfectly.
- Degraded PDFs with jittery fonts or OCR-layer artifacts can yield imperfect
  heading detection; source artifacts are reproduced faithfully.

## License

MIT
