# Conversion Pipeline

How `pdf_to_epub.py` turns a PDF into a structured EPUB.

```mermaid
flowchart TD
    A[Input PDF] --> B["Extract positioned blocks<br/>(PyMuPDF: text spans, images, bboxes)"]
    B --> C{Per-page analysis}

    C --> D["Boilerplate signatures<br/>edge-zone text repeated on ≥40% of pages"]
    C --> E["Footnote regions<br/>superscript markers · gap + size + position"]
    C --> F["Column ordering<br/>full-width vs left/right segments"]

    D & E & F --> G{Classify each block}

    G -->|type=image| H["Figure<br/>dedupe by hash, drop <2KB"]
    G -->|footnote zone| I["Notes collection<br/>(number, html)"]
    G -->|standalone large line| J["Heading h1/h2"]
    G -->|≥1.15× body, title zone| K["Byline group"]
    G -->|small inset, pages 0–1| L["Abstract<br/>inject h2"]
    G -->|otherwise| M["Text lines"]

    M --> N{"Paragraph merge<br/>indent >4pt or prev ends '.'?"}
    N -->|new| O["Close &lt;p&gt;, start next"]
    N -->|continue| O2["Append to open &lt;p&gt;<br/>(spans columns &amp; pages)"]

    J --> P["Chapter boundaries<br/>(split at heading fragments)"]
    O & O2 --> P
    I --> Q["Notes chapter<br/>+ sup link backfill"]
    H --> R[ebooklib packaging]
    P --> R
    Q --> R
    R --> S["EPUB<br/>spine · TOC · nav"]

    style A fill:#eef,stroke:#66c
    style S fill:#efe,stroke:#6c6
```

## Detection signals

| Element | Signal |
|---|---|
| Body font size | Modal span size across the document |
| Line height | Median line bbox height |
| h1 | span size ≥ 1.6× body |
| h2 | standalone single-line block ≥ 1.08× body, no mid-string `.` |
| Byline | block median ≥ 1.15× body + short single-line followers |
| Abstract | median in (0.75–0.92)× body, inset margins, pages 0–1 |
| Footnote | opens with superscript digit in lower 45% of page |
| Unnumbered note | bottom 40%, median <0.95× body, gap >2× line height |
| Header/footer | short text in page edge zones on ≥40% of pages |
| New paragraph | first-line indent >4pt, or previous line ends `.`/`!`/`?` |
| Column split | block crosses page midline = full-width; else L/R by center |
