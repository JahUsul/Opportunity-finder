from datetime import date, datetime, timedelta

import pytest

from opfinder.dedup import DEDUP_WINDOW_DAYS, DedupStore, _normalize, compute_hash
from opfinder.models import Candidate


def make_candidate(**overrides) -> Candidate:
    defaults = dict(
        id="test-id",
        source="reddit",
        source_url="https://example.com/post/1",
        author_id="alice",
        title="My app crashes when I export CSVs",
        body="full body text",
        raw_excerpt="excerpt",
        scraped_at=datetime(2026, 5, 18),
    )
    defaults.update(overrides)
    return Candidate(**defaults)


@pytest.fixture
def store(tmp_path):
    s = DedupStore(tmp_path / "test.db", today=date(2026, 5, 18))
    yield s
    s.close()


def test_normalize_lowercases_and_strips_non_alphanumeric():
    assert _normalize("Hello, World! 123") == "helloworld123"
    assert _normalize("Mixed-Case_Text") == "mixedcasetext"
    assert _normalize("") == ""


def test_compute_hash_stable_across_capitalization_and_punctuation():
    assert compute_hash("reddit", "Hello, World!", "alice") == compute_hash(
        "reddit", "hello world", "alice"
    )


def test_compute_hash_changes_with_source_or_author():
    base = compute_hash("reddit", "Same Title", "alice")
    assert base != compute_hash("hn", "Same Title", "alice")
    assert base != compute_hash("reddit", "Same Title", "bob")


def test_new_candidate_is_inserted_and_returned(store):
    c = make_candidate()
    survivors = store.filter_new([c])
    assert survivors == [c]
    assert c.dedup_hash
    stats = store.stats()
    assert stats["total"] == 1
    assert stats["by_status"] == {"active": 1}


def test_repeat_within_window_is_skipped(store):
    store.filter_new([make_candidate()])
    again = store.filter_new([make_candidate()])
    assert again == []


def test_repeat_older_than_window_resurfaces(tmp_path):
    db = tmp_path / "resurface.db"
    earlier = DedupStore(db, today=date(2026, 4, 1))
    earlier.filter_new([make_candidate()])
    earlier.close()

    later = DedupStore(db, today=date(2026, 5, 18))  # 47 days later
    survivors = later.filter_new([make_candidate()])
    assert len(survivors) == 1
    later.close()


def test_exactly_window_boundary_does_not_resurface(tmp_path):
    db = tmp_path / "boundary.db"
    earlier = DedupStore(db, today=date(2026, 4, 20))
    earlier.filter_new([make_candidate()])
    earlier.close()

    later = DedupStore(db, today=date(2026, 4, 20) + timedelta(days=DEDUP_WINDOW_DAYS))
    survivors = later.filter_new([make_candidate()])
    assert survivors == []
    later.close()


def test_ignored_forever_never_resurfaces(tmp_path):
    db = tmp_path / "ignored.db"
    store = DedupStore(db, today=date(2026, 1, 1))
    [c] = store.filter_new([make_candidate()])
    store.mark_ignored([c.dedup_hash])
    store.close()

    later = DedupStore(db, today=date(2026, 12, 1))  # well beyond the window
    survivors = later.filter_new([make_candidate()])
    assert survivors == []
    later.close()


def test_mark_ignored_with_empty_list_is_noop(store):
    store.mark_ignored([])


def test_stats_counts_by_status_and_source(store):
    store.filter_new(
        [
            make_candidate(title="A", author_id="a"),
            make_candidate(title="B", author_id="b", source="hn"),
            make_candidate(title="C", author_id="c", source="hn"),
        ]
    )
    stats = store.stats()
    assert stats["total"] == 3
    assert stats["by_status"] == {"active": 3}
    assert stats["by_source"] == {"reddit": 1, "hn": 2}
    assert stats["recent"] == 3
    assert stats["old"] == 0


def test_filter_new_idempotent_same_week(store):
    first = store.filter_new([make_candidate()])
    second = store.filter_new([make_candidate()])
    assert len(first) == 1
    assert second == []


def test_filter_new_updates_last_seen_but_preserves_first_seen(tmp_path):
    import sqlite3

    db = tmp_path / "update.db"
    s = DedupStore(db, today=date(2026, 5, 1))
    s.filter_new([make_candidate()])
    s.close()

    s = DedupStore(db, today=date(2026, 5, 10))
    s.filter_new([make_candidate()])
    s.close()

    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT first_seen_week, last_seen_week FROM seen_candidates"
    ).fetchone()
    conn.close()
    assert row == ("2026-05-01", "2026-05-10")


def test_distinct_candidates_in_same_batch_all_survive(store):
    survivors = store.filter_new(
        [
            make_candidate(title="A", author_id="a"),
            make_candidate(title="B", author_id="b"),
        ]
    )
    assert len(survivors) == 2
    hashes = {c.dedup_hash for c in survivors}
    assert len(hashes) == 2


def test_dedup_hash_populated_if_missing(store):
    c = make_candidate()
    assert c.dedup_hash == ""
    store.filter_new([c])
    assert c.dedup_hash == compute_hash(c.source, c.title, c.author_id)
