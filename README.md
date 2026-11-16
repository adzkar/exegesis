# exegesis

> *exegesis* — Greek ἐξήγησις, "to lead out." This tool leads the text out of
> its fixed layout.

A PDF → EPUB converter tuned for **multi-column academic papers and
journals**. Pure Python, rule-based layout analysis, no OCR or ML
dependencies.

## Features

- **Multi-column reading order** — a document-wide vote on recurring x0
  positions seeds the column grid, then each page recovers its own
  geometry; columns emit left to right, section bands spanning them stay
  sequential, and outdent/equation fragments fold out rather than seeding
  phantom columns. Handles 1, 2, or 3-column layouts.
- **Paragraph reconstruction** — merges mid-sentence splits across blocks,
  columns, and page breaks; indented or post-sentence blocks start new
  paragraphs.
- **Table of contents & chapters** — headings detected by font analysis
  become a navigable TOC, and chapters split exactly at each heading.
- **Footnote extraction** — numbered footnotes *and* unnumbered author notes
  (below a rule/gap) move to a linked **Notes** chapter; in-body superscripts
  become clickable references.
- **Header/footer stripping** — running heads and page numbers detected by
  repetition, pattern matching, or margin geometry and removed.
- **Front-matter handling** — title, byline/affiliation, and abstract are
  detected and kept as distinct, well-structured elements.
- **Marginalia & asides** — margin notes and front-matter info boxes lift
  into `<aside>` regions instead of interleaving with body text.
- **Invisible-text filtering** — spans painted near-white on a light
  ground (publisher placeholders) drop at extraction.
- **Structure preservation** — numbered lists, blockquotes, bold/italic
  styling, and rotated tables (re-rendered upright as images).
- **Image extraction** — embedded figures are deduplicated and included;
  figure regions rasterize rather than emitting stray labels as prose.

## Requirements

```bash
pip3 install -r requirements.txt   # pymupdf, ebooklib
```

## Usage

```bash
python3 pdf_to_epub.py paper.pdf                      # -> paper.epub
python3 pdf_to_epub.py paper.pdf -o out.epub
python3 pdf_to_epub.py paper.pdf --title "My Paper" --author "J. Doe"
python3 pdf_to_epub.py paper.pdf --lang de            # German patterns + metadata
python3 pdf_to_epub.py paper.pdf --keep-headers       # keep headers/footers
python3 pdf_to_epub.py --version                      # print version
```

`--lang` selects the pattern profile for furniture, note openers, section
labels, and figure/table captions — `en` (default), `de`, `fr`, `es` — and
stamps the EPUB's language metadata. The API also accepts a custom
`LangProfile` for unsupported languages.

## How it works

1. **Extract** — PyMuPDF yields positioned text/image blocks per page.
2. **Order** — spanning blocks and section bands set the sequence; within
   them the page's column grid reads top to bottom, left column then right.
3. **Render** — blocks become headings, bylines, paragraphs, footnotes, or
   figures based on font size, indentation, position, and repetition.
4. **Emit** — fragments are chunked into chapters at headings, footnotes are
   collected into a Notes section, and everything is packaged via ebooklib.

See the [pipeline diagram](dev/pipeline.md) for a visual walkthrough and
[AGENTS.md](AGENTS.md) for the detailed heuristics.

## Evaluating a conversion

`python3 scripts/eval_epub.py paper.pdf paper.epub [--lang en|de|fr|es]`
compares the EPUB against its source and prints a severity-ranked
markdown report — container, metadata, text coverage, TOC, figures, and
classified word loss. It exits 1 on any High finding (the CI gate), 0
when clean, and 2 on operational failure.

## Conversion report

`python3 scripts/build_report.py` converts every `*.pdf` at the repo
root (PDFs are gitignored — supply your own corpus) and regenerates
`docs/report/index.html` + `report.json`, the per-paper layout/stats
table served by GitHub Pages. Run it before a release to refresh the
published numbers.

## Limitations

- **No OCR.** Scanned PDFs without a text layer produce image-only output —
  an OCR pass (e.g. tesseract) would be needed as a separate step.
- **Latin-script languages** — profiles exist for English, German, French,
  and Spanish (`--lang`); other languages are rejected by `--lang` but can
  still be converted under the default English profile (detection quality
  degrades) or a custom `LangProfile` via `convert()`. CJK and RTL scripts
  need word-shaping/vertical-text and direction review — out of scope today.
- Complex layouts (dense figures, equations, tables) may read imperfectly.
- Degraded PDFs with jittery fonts or OCR-layer artifacts can yield imperfect
  heading detection; source artifacts are reproduced faithfully.

## License

[MIT](LICENSE.md)
