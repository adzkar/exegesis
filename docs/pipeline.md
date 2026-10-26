# Conversion Pipeline

How `pdf_to_epub.py` turns a PDF into a structured EPUB.

```mermaid
flowchart TD
    A[Input PDF] --> B["Extract positioned blocks<br/>(PyMuPDF: text spans, images, bboxes)"]
    B --> C{Document analysis}

    C --> D["Column edges<br/>x0 clusters on ≥50% of pages"]
    C --> E["Boilerplate + furniture<br/>repetition sigs · edge patterns · margin strips"]
    C --> F["Body font &amp; line height<br/>modal size · median height"]

    D & E & F --> G{Per-page ordering}
    G -->|"top zone" (above first span)| H["Header zone blocks"]
    G -->|"per-column sequence"| I["Column blocks + covering spans<br/>interleaved by y, deduped"]

    H & I --> J{Classify each block}

    J -->|type=image| K["Figure<br/>dedupe by hash, drop <2KB"]
    J -->|footnote zone| L["Notes collection"]
    J -->|all-caps, top 12%| M["&lt;p class=label&gt;<br/>(BREVIA etc.)"]
    J -->|heading font| N["h1/h2<br/>single-line ≥1.08× · multi-line ≥1.6×"]
    J -->|size or after-h1 + name-like| O["&lt;p class=byline&gt;<br/>+ drop-cap relocation"]
    J -->|"References"/"Methods"/etc| N
    J -->|small inset, top half| P["Abstract → inject h2"]
    J -->|otherwise| Q["Text lines<br/>+ &lt;b&gt;/&lt;i&gt; via font names"]

    Q --> R{"Paragraph merge<br/>indent >4pt or prev ends '.'?<br/>drop-cap seed never flushes"}
    N --> S["Chapter boundaries<br/>(split at heading fragments)"]
    R --> S
    L --> T["Notes chapter<br/>+ sup link backfill"]
    K --> U[ebooklib packaging]
    S --> U
    T --> U
    U --> V["EPUB<br/>spine · TOC · nav<br/>validated title/author meta"]

    style A fill:#eef,stroke:#66c
    style V fill:#efe,stroke:#6c6
```

## Detection signals

| Element | Signal |
|---|---|
| Body font size | Modal span size across the document |
| Line height | Median line bbox height |
| Column edges | x0 clusters found on ≥50% of pages |
| Full-width span | block crosses a gutter between column edges |
| Top zone | blocks fully above the first spanning element |
| h1 | single-line ≥1.6× body, or multi-line block all-lines ≥1.6× |
| h2 | standalone single-line ≥1.08× body, no mid-string `.` |
| Section label | `References`, `Supporting Online Material`, `Methods`, … at block start → h2 |
| Top label | all-caps short text in top 12% of page → `<p class="label">` |
| Byline | median ≥1.15× body, or directly after h1 + name-like (` and `/`,`); short single-line followers merge; trailing drop-cap letter relocates into the paragraph |
| Abstract | median in (0.75–0.92)× body, inset margins, top half of page 0–1 |
| Footnote | opens with superscript digit in lower 45% of page |
| Unnumbered note | bottom 40%, median <0.95× body, gap >2× line height |
| Header/footer | signature repeated on ≥40% of pages in edge zone |
| Furniture | pattern (`VOL`, `www.`, `ISSN`, `doi`, `©`, dates) in outer 7% edge |
| Margin strip | <25pt wide, >4× tall, in outer 10% margin (rotated sidebars) |
| New paragraph | first-line indent >4pt, or previous line ends `.`/`!`/`?` |
| Drop-cap | single uppercase fragment (not `I`/`A`) + lowercase text → joined |
| Bold | flags bit 16, or font name `.B`/`Bold` suffix |
| Italic | font name `.I`/`Italic`/`Oblique` suffix |
| Metadata title | must contain letters, not pagination junk → h1/filename fallback |
| Metadata author | invalid/empty → byline text fallback |
