"""Expected failures are limited to documented semantic mismatches, not setup errors."""


class SemanticMismatch(AssertionError):
    """Mark only a known reading-order/content defect as an expected failure."""


def require(condition, message):
    """Keep unexpected assertions distinguishable from an owned semantic symptom."""
    if not condition:
        raise SemanticMismatch(message)


def assert_retained_once(text, *needles):
    """Missing or duplicated required body content fails normally, never as an ordering xfail."""
    counts = {needle: text.count(needle) for needle in needles}
    unexpected = {needle: count for needle, count in counts.items() if count != 1}
    assert not unexpected, f"required body content must survive exactly once: {unexpected}"


def require_order(text, *needles, issue):
    """Expect only wrong order; missing/duplicated sentinels are unrelated hard failures."""
    assert_retained_once(text, *needles)
    positions = [text.find(needle) for needle in needles]
    require(
        positions == sorted(positions),
        f"{issue}: expected {' -> '.join(needles)}; observed positions {positions}",
    )


def require_absent(text, *needles, issue):
    """Identify the exact leaked furniture or figure labels rather than any failure."""
    leaked = [needle for needle in needles if needle in text]
    require(not leaked, f"{issue}: unwanted body-flow text {leaked!r}")
