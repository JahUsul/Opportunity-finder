from datetime import datetime

from opfinder.models import Candidate


def _make() -> Candidate:
    return Candidate(
        id="x",
        source="reddit",
        source_url="https://example.com/post/1",
        author_id="alice",
        title="t",
        body="b",
        raw_excerpt="e",
        scraped_at=datetime(2026, 5, 18),
    )


def test_injection_patterns_defaults_to_empty_list():
    c = _make()
    assert c.injection_patterns == []
    assert isinstance(c.injection_patterns, list)


def test_injection_flag_defaults_to_false():
    assert _make().injection_flag is False


def test_injection_patterns_is_per_instance_not_shared():
    a = _make()
    b = _make()
    a.injection_patterns.append("ignore_previous_instructions")
    assert b.injection_patterns == []
