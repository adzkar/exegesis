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
    G -->|top zone above first span| H["Header zone blocks"]
    G -->|per-column sequence| I["Column blocks + covering spans<br/>interleaved by y, deduped"]

    H & I --> J{Classify each block}

    J -->|image| K["Figure<br/>dedupe by hash, drop under 2KB"]
    J -->|footnote zone| L["Notes collection"]
    J -->|rotated table region| K2["Rasterize clip<br/>mediabox rect via rotation_matrix"]
    J -->|all-caps top label| M["&lt;p class=label&gt;<br/>(BREVIA etc.)"]
    J -->|heading font| N["h1/h2<br/>size-based · all-bold · named labels<br/>first p0 heading ≥1.3× promotes h1"]
    J -->|named section label| N
    J -->|byline size or position| O["&lt;p class=byline&gt;<br/>+ drop-cap relocation"]
    J -->|small inset top half| P["Abstract - inject h2"]
    J -->|indented and isolated| Q2["&lt;blockquote&gt;<br/>quotes, verse"]
    J -->|otherwise| Q["Text lines<br/>+ &lt;b&gt;/&lt;i&gt; via font names<br/>+ N. lines as list items"]

    Q --> R{"Paragraph merge<br/>indent &gt;4pt or prev ends period<br/>drop-cap seed never flushes"}
    N --> S["Chapter boundaries<br/>(split at heading fragments)"]
    R --> S
    Q2 --> S
    L --> T["Notes chapter<br/>+ sup link backfill"]
    K --> U[ebooklib packaging]
    K2 --> U
    S --> U
    T --> U
    U --> V["EPUB<br/>spine · TOC · nav · style.css<br/>validated title/author meta"]

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
| h1 | single-line ≥1.6× body, multi-line block all-lines ≥1.6×, or first p0 heading ≥1.3× (promoted) |
| h2 | standalone ≥1.08× body, or all-bold line/block ≥0.9× body outside edge zones that precedes body-flow text in its column (a heading terminates the follower scan; demotion silences bold-band lines per line and in mixed blocks, though a demoted block's ≥1.3× lines may still emit; the scan is column- and page-local so a column- or page-final bold line stays demoted); no sentence periods, no `*†‡§` opener |
| Section label | `References`, `Supporting Online Material`, `Methods`, … at block start → h2 (respects bold-gate demotion — `Funding …` tails are notes) |
| Top label | all-caps short text in top 12% of page → `<p class="label">` |
| Byline | median ≥1.15× body, or directly after h1 + name-like (` and `/`,`); short single-line followers merge; trailing drop-cap letter relocates into the paragraph |
| Abstract | median in (0.75–0.92)× body, inset margins, top half of page 0–1 |
| Footnote | opens with superscript digit in lower 45% of page |
| Unnumbered note | bottom 40% + gap >2× line height + <0.95× body, OR acknowledgment opener in bottom 55% |
| Page number | pure-digit block in outer 7% edge → dropped |
| List item | line starting `N.`/`N)` → `<p class="listitem">`; absorbs continuation lines |
| Blockquote | block indented >12pt from column edge AND isolated (gap >0.7× line height both sides; kept furniture doesn't count as a neighbor) |
| Rotated table | ≥3 tall-narrow blocks sharing a y-band → region rasterized; clip mapped mediabox→displayed via `page.rotation_matrix` |
| Header/footer | signature repeated on ≥40% of pages in edge zone (set built at any doc length; under `--keep-headers` matches flag `is_furniture`) |
| Furniture | pattern (`VOL`, `www.`, `ISSN`, `doi`, `©`, dates) in outer 7% edge; under `--keep-headers` flagged `is_furniture` and emitted verbatim at page edges — inert to classifiers, ordering machinery, and table regions |
| Margin strip | <25pt wide, >4× tall, in outer 10% margin (rotated sidebars) |
| New paragraph | first-line indent >4pt, or previous line ends `.`/`!`/`?` |
| Drop-cap | single uppercase fragment (not `I`/`A`) + lowercase text → joined |
| Bold | flags bit 16, or font name `.B`/`Bold` suffix |
| Italic | font name `.I`/`Italic`/`Oblique` suffix |
| Metadata title | must contain letters, not pagination junk → h1/filename fallback |
| Metadata author | invalid/empty → byline text fallback |
