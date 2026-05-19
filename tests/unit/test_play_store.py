import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from opfinder.scrapers.play_store import PlayStoreScraper

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "play_store"


def load_reviews() -> list[dict]:
    raw = json.loads((FIXTURES / "reviews.json").read_text())
    for r in raw:
        if isinstance(r.get("at"), str):
            r["at"] = datetime.fromisoformat(r["at"]).replace(tzinfo=timezone.utc)
    return raw


def make_reviews_fn(reviews_by_app: dict[str, list[dict]], *, fail_ids: set[str] = frozenset()):
    calls: list[tuple[str, dict]] = []

    def reviews_fn(app_id, **kwargs):
        calls.append((app_id, kwargs))
        if app_id in fail_ids:
            raise RuntimeError("play store says no")
        return reviews_by_app.get(app_id, []), None

    reviews_fn.calls = calls  # type: ignore[attr-defined]
    return reviews_fn


NOW = datetime(2026, 5, 18, tzinfo=timezone.utc)
SINCE = NOW - timedelta(days=7)


def test_happy_path_from_canned_fixture():
    reviews = load_reviews()
    fn = make_reviews_fn({"com.example.tracker": reviews})
    scraper = PlayStoreScraper(
        app_ids=[{"id": "com.example.tracker", "name": "Tracker"}],
        reviews_fn=fn,
    )
    candidates = scraper.fetch(SINCE)
    # 5 rows: 1 is 5-star (filtered), 1 is too old (filtered) → 3 survivors.
    assert len(candidates) == 3
    sample = next(c for c in candidates if "Sync is broken" in c.body)
    assert sample.source == "play_store"
    assert sample.source_url == "https://play.google.com/store/apps/details?id=com.example.tracker"
    assert sample.author_id == "ops_lead"


def test_title_is_prefixed_with_app_name_and_uses_content_snippet():
    reviews = load_reviews()
    fn = make_reviews_fn({"com.example.tracker": reviews})
    scraper = PlayStoreScraper(
        app_ids=[{"id": "com.example.tracker", "name": "Tracker"}],
        reviews_fn=fn,
    )
    candidates = scraper.fetch(SINCE)
    for c in candidates:
        assert c.title.startswith("[Tracker] ")
    # Snippet should be a prefix of the body content.
    sample = next(c for c in candidates if "Sync is broken" in c.body)
    assert "Sync is broken" in sample.title


def test_long_content_snippet_is_truncated():
    long_content = ("word " * 50).strip()
    fn = make_reviews_fn(
        {
            "com.example.long": [
                {
                    "reviewId": "r1",
                    "userName": "alice",
                    "content": long_content,
                    "score": 2,
                    "at": NOW - timedelta(days=1),
                }
            ]
        }
    )
    scraper = PlayStoreScraper(
        app_ids=[{"id": "com.example.long", "name": "Wordy"}],
        reviews_fn=fn,
    )
    [c] = scraper.fetch(SINCE)
    assert c.title.startswith("[Wordy] ")
    assert c.title.endswith("...")
    # The bracket-prefixed title should remain reasonably short.
    assert len(c.title) <= len("[Wordy] ") + 80 + 3


def test_four_and_five_star_reviews_excluded():
    reviews = load_reviews()
    fn = make_reviews_fn({"com.example.tracker": reviews})
    scraper = PlayStoreScraper(
        app_ids=[{"id": "com.example.tracker", "name": "Tracker"}],
        reviews_fn=fn,
    )
    candidates = scraper.fetch(SINCE)
    assert all("Best PM tool" not in c.body for c in candidates)


def test_date_filter_excludes_old_reviews():
    reviews = load_reviews()
    fn = make_reviews_fn({"com.example.tracker": reviews})
    scraper = PlayStoreScraper(
        app_ids=[{"id": "com.example.tracker", "name": "Tracker"}],
        reviews_fn=fn,
    )
    candidates = scraper.fetch(SINCE)
    assert all("From before the window" not in c.body for c in candidates)


def test_missing_reviewer_name_becomes_deleted():
    reviews = load_reviews()
    fn = make_reviews_fn({"com.example.tracker": reviews})
    scraper = PlayStoreScraper(
        app_ids=[{"id": "com.example.tracker", "name": "Tracker"}],
        reviews_fn=fn,
    )
    candidates = scraper.fetch(SINCE)
    anon = [c for c in candidates if "no name here" in c.body]
    assert len(anon) == 1
    assert anon[0].author_id == "deleted"


def test_per_app_exception_logged_and_others_continue(caplog):
    reviews = load_reviews()
    fn = make_reviews_fn(
        {"com.example.tracker": reviews},
        fail_ids={"com.example.broken"},
    )
    scraper = PlayStoreScraper(
        app_ids=[
            {"id": "com.example.broken", "name": "Broken App"},
            {"id": "com.example.tracker", "name": "Tracker"},
        ],
        reviews_fn=fn,
    )
    with caplog.at_level(logging.ERROR):
        candidates = scraper.fetch(SINCE)
    assert len(candidates) >= 1
    assert all("[Tracker]" in c.title for c in candidates)
    assert any(
        "broken" in rec.message.lower() or "Broken App" in rec.message
        for rec in caplog.records
    )


def test_empty_app_ids_yields_nothing():
    fn = make_reviews_fn({})
    scraper = PlayStoreScraper(app_ids=[], reviews_fn=fn)
    assert scraper.fetch(SINCE) == []


def test_reviews_fn_receives_lang_country_count():
    reviews = load_reviews()
    fn = make_reviews_fn({"com.example.tracker": reviews})
    scraper = PlayStoreScraper(
        app_ids=[{"id": "com.example.tracker", "name": "Tracker"}],
        reviews_fn=fn,
        lang="de",
        country="de",
        count=42,
    )
    scraper.fetch(SINCE)
    assert fn.calls
    app_id, kwargs = fn.calls[0]
    assert app_id == "com.example.tracker"
    assert kwargs["lang"] == "de"
    assert kwargs["country"] == "de"
    assert kwargs["count"] == 42


def test_result_returned_as_plain_list_is_accepted():
    """Some callers may pass a function that returns just the list, not a (list, token) tuple."""
    reviews = load_reviews()

    def fn(app_id, **kwargs):
        return reviews

    scraper = PlayStoreScraper(
        app_ids=[{"id": "com.example.tracker", "name": "Tracker"}],
        reviews_fn=fn,
    )
    candidates = scraper.fetch(SINCE)
    assert len(candidates) == 3
