"""Guard the distinction between semantic regressions and unrelated test failures."""
import fitz
import pytest
from builders import _page, _save, paragraph, text
from helpers import chapter_body_text
from layout_assertions import SemanticMismatch, require, require_absent, require_order

from exegesis import convert


def test_body_order_excludes_repeated_xhtml_chapter_title(tmp_path):
    """A chapter title must not pretend a later heading precedes leading body prose."""
    doc = fitz.open()
    page = _page(doc)
    paragraph(page, (72, 190, 540, 250), "Leading body belongs before its later heading.")
    text(page, 72, 350, "Later Heading", size=18)
    paragraph(page, (72, 440, 540, 500), "Following body closes the section.")
    pdf = _save(doc, tmp_path / "chapter-title.pdf")
    out = pdf.with_suffix(".epub")
    convert(pdf, out)
    body = chapter_body_text(out)
    assert body.count("Later Heading") == 1
    assert body.index("Leading body") < body.index("Later Heading") < body.index("Following body")


def test_semantic_checks_accept_ordered_retained_body():
    """The helper checks content and order, not snapshot formatting or byte identity."""
    require_order("opening arbitrary middle closing", "opening", "closing", issue="control")
    require_absent("ordinary body", "axis-label", issue="control")
    require(True, "a passing condition must not raise")


def test_semantic_order_failure_is_dedicated():
    """Only a known reordering uses the type permitted by the ordering xfails."""
    with pytest.raises(SemanticMismatch, match="owned issue"):
        require_order("second first", "first", "second", issue="owned issue")


@pytest.mark.parametrize("text", ("first only", "unrelated content", "first second first"))
def test_missing_or_duplicated_content_is_not_expected_ordering(text):
    """Content loss/duplication cannot masquerade as the documented ordering symptom."""
    with pytest.raises(AssertionError) as error:
        require_order(text, "first", "second", issue="owned issue")
    assert type(error.value) is AssertionError


def test_semantic_contamination_failure_names_the_leak():
    """A furniture/figure leak reports its actual offending content."""
    with pytest.raises(SemanticMismatch, match="axis-label"):
        require_absent("prose axis-label prose", "axis-label", issue="owned issue")


def test_semantic_assertions_do_not_wrap_unexpected_errors():
    """A setup/programming error must propagate, not become an expected semantic failure."""
    with pytest.raises(AttributeError):
        require_order(None, "first", "second", issue="owned issue")
    assert not issubclass(AssertionError, SemanticMismatch)
    assert not issubclass(RuntimeError, SemanticMismatch)
