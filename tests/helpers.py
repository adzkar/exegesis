"""EPUB inspection utilities: spine/fragment snapshots, link and manifest lint.

Goldens compare the *shape* of the output (element sequence + short text
prefixes + lengths), not the rendered bytes — enough to catch reading-order
and classification regressions without brittle full-text comparisons.
"""
from __future__ import annotations

import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCTYPE_RE = re.compile(r"<!DOCTYPE[^>]*>")
TOP_TAGS = {"h1", "h2", "h3", "p", "blockquote"}
OPF_NS = "{http://www.idpf.org/2007/opf}"
CONTAINER_NS = "{urn:oasis:names:tc:opendocument:xmlns:container}"


def corpus_pdf(name):
    """Path of a gitignored corpus PDF; caller's test skips when absent."""
    p = ROOT / name
    if not p.is_file():
        pytest.skip(f"corpus PDF not present: {name}")
    return p


class _FragParser(HTMLParser):
    """Collect top-level ``<body>`` children as (tag, text) pairs, plus all
    element ids and a/src references for cross-reference linting."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.fragments: list[tuple[str, str]] = []
        self.ids: set[str] = set()
        self.refs: list[str] = []
        self._cur_tag: str | None = None
        self._cur_parts: list[str] = []
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        id_attr = attrs.get("id")
        if id_attr:
            self.ids.add(id_attr)
        for key in ("href", "src"):
            ref = attrs.get(key)
            if ref:
                self.refs.append(ref)
        if self._cur_tag is not None:
            self._depth += 1
        elif tag in TOP_TAGS:
            self._cur_tag = tag
            self._cur_parts = []
            self._depth = 1

    def handle_endtag(self, tag):
        if self._cur_tag is None:
            return
        self._depth -= 1
        if self._depth <= 0:
            text = " ".join("".join(self._cur_parts).split())
            self.fragments.append((self._cur_tag, text))
            self._cur_tag = None
            self._depth = 0

    def handle_data(self, data):
        if self._cur_tag is not None:
            self._cur_parts.append(data)


class _TocParser(HTMLParser):
    """Nav ``epub:type="toc"`` entries as (ol_depth, text, href) triples."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.entries: list[tuple[int, str, str]] = []
        self._in_toc = False
        self._ol = 0
        self._a: tuple[int, str, list[str]] | None = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "nav":
            self._in_toc = attrs.get("epub:type") == "toc"
            self._ol = 0
        elif self._in_toc and tag == "ol":
            self._ol += 1
        elif self._in_toc and tag == "a":
            self._a = (self._ol, attrs.get("href") or "", [])

    def handle_data(self, data):
        if self._a is not None:
            self._a[2].append(data)

    def handle_endtag(self, tag):
        if tag == "nav":
            self._in_toc = False
        elif self._in_toc and tag == "ol":
            self._ol -= 1
        elif self._in_toc and tag == "a" and self._a is not None:
            text = " ".join("".join(self._a[2]).split())
            self.entries.append((self._a[0], text, self._a[1]))
            self._a = None


def _opf_root(zf):
    container = ET.fromstring(zf.read("META-INF/container.xml"))
    rootfile = container.find(f"{CONTAINER_NS}rootfiles/{CONTAINER_NS}rootfile")
    assert rootfile is not None, "container.xml has no rootfile element"
    opf_path = rootfile.get("full-path")
    assert opf_path, "rootfile element has no full-path"
    return opf_path, ET.fromstring(zf.read(opf_path))


def _resolve(base_file, target):
    """Resolve a relative href against a file's zip path."""
    if "/" in base_file:
        return base_file.rsplit("/", 1)[0] + "/" + target
    return target


def spine_files(zf):
    """Ordered zip paths of spine documents (reading order)."""
    opf_path, opf = _opf_root(zf)
    manifest = {
        it.get("id"): it.get("href")
        for it in opf.find(f"{OPF_NS}manifest") or []
    }
    spine = [
        manifest[ref.get("idref")]
        for ref in opf.find(f"{OPF_NS}spine") or []
    ]
    base = opf_path.rsplit("/", 1)[0]
    return [f"{base}/{h}" for h in spine]


def snapshot_epub(path) -> dict[str, Any]:
    """Golden snapshot dict: TOC levels/texts + per-spine-doc fragment
    signatures ``[tag, first-60-chars, char-count]``."""
    out: dict[str, Any] = {"toc": [], "spine": []}
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        for name in names:
            if name.endswith("nav.xhtml"):
                p = _TocParser()
                p.feed(zf.read(name).decode("utf8"))
                out["toc"] = [[lv, t] for lv, t, _h in p.entries]
        for name in spine_files(zf):
            if name.endswith("nav.xhtml"):
                continue
            p = _FragParser()
            p.feed(zf.read(name).decode("utf8"))
            out["spine"].append(
                {
                    "file": name.rsplit("/", 1)[-1],
                    "frags": [
                        [tag, text[:60], len(text)]
                        for tag, text in p.fragments
                    ],
                }
            )
    return out


def doc_html(path):
    """Map basename -> raw XHTML for every document in the EPUB."""
    out = {}
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            if name.endswith(".xhtml"):
                out[name.rsplit("/", 1)[-1]] = zf.read(name).decode("utf8")
    return out


def doc_texts(path):
    """Map basename -> normalized text for every chapter/notes doc."""
    return {
        name: " ".join(re.sub(r"<[^>]+>", " ", html).split())
        for name, html in doc_html(path).items()
    }


def chapter_text(path):
    """All chapter docs' text concatenated in spine order."""
    texts = doc_texts(path)
    return "\n".join(
        t for name, t in sorted(texts.items()) if name.startswith("chap_")
    )


def lint_epub(path):
    """Structural lint of a finished EPUB; returns a list of problems.

    Checks: every .xhtml parses as XML; every href/src target exists and
    every ``#anchor`` resolves to an id in the target doc; every manifest
    item is present in the archive.
    """
    problems = []
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        docs = {}
        for name in names:
            if not name.endswith(".xhtml"):
                continue
            raw = zf.read(name).decode("utf8")
            try:
                ET.fromstring(DOCTYPE_RE.sub("", raw))
            except ET.ParseError as e:
                problems.append(f"{name}: malformed XHTML: {e}")
            p = _FragParser()
            p.feed(raw)
            docs[name] = p
        for name, p in docs.items():
            for ref in p.refs:
                target, _, anchor = ref.partition("#")
                if not target:
                    full = name
                else:
                    full = _resolve(name, target)
                if target and full not in names:
                    problems.append(f"{name}: ref to missing {ref}")
                    continue
                if anchor and anchor not in docs.get(full, _FragParser()).ids:
                    problems.append(f"{name}: dead anchor {ref}")
        _opf_path, opf = _opf_root(zf)
        for it in opf.find(f"{OPF_NS}manifest") or []:
            href = _resolve(_opf_path, it.get("href") or "")
            if href not in names:
                problems.append(f"manifest item missing from archive: {href}")
    return problems
