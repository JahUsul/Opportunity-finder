import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from opfinder.scrapers.app_store import AppStoreScraper

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "app_store"


def load_reviews() -> list[dict]:
    raw = json.loads((FIXTURES / "reviews.json").read_text())
    for r in raw:
        if isinstance(r.get("date"), str):
            r["date"] = datetime.fromisoformat(r["date"]).replace(tzinfo=timezone.utc)
    return raw


class AppStoreStub:
    """Mimics `app_store_scraper.AppStore`: holds `reviews` and a no-op `review()`."""

    def __init__(self, reviews: list[dict]) -> None:
        self.reviews = reviews
        self.review_calls: list[dict] = []

    def review(self, **kwargs):
        self.review_calls.append(kwargs)


def make_factory(reviews_by_id: dict[str, list[dict]], *, fail_ids: set[str] = frozenset()):
    def factory(country, app_name, app_id):
        if str(app_id) in fail_ids:
            raise RuntimeError("apple says no")
        return AppStoreStub(reviews_by_id.get(str(app_id), []))

    return factory


NOW = datetime(2026, 5, 18, tzinfo=timezone.utc)
SINCE = NOW - timedelta(days=7)


def test_happy_path_from_canned_fixture():
    reviews = load_reviews()
    factory = make_factory({"100": reviews})
    scraper = AppStoreScraper(
        app_ids=[{"id": "100", "name": "Calendar Plus", "slug": "calendar-plus"}],
        scraper_factory=factory,
    )
    candidates = scraper.fetch(SINCE)
    # Out of 5 fixture rows: 1 is 5-star (filtered), 1 is too old (filtered)
    # → 3 survivors.
    assert len(candidates) == 3
    titles = {c.title for c in candidates}
    assert "[Calendar Plus] Crashes every time I open a recurring event" in titles
    assert "[Calendar Plus] Where did the iPad layout go?" in titles
    sample = next(c for c in candidates if "Crashes" in c.title)
    assert sample.source == "app_store"
    assert sample.source_url == "https://apps.apple.com/us/app/calendar-plus/id100"
    assert sample.author_id == "frustrated_pm"
    assert "Sync to Google is unreliable" in sample.body
    assert sample.raw_excerpt == sample.body[:500]


def test_title_is_prefixed_with_app_name():
    reviews = load_reviews()
    factory = make_factory({"100": reviews})
    scraper = AppStoreScraper(
        app_ids=[{"id": "100", "name": "Calendar Plus", "slug": "calendar-plus"}],
        scraper_factory=factory,
    )
    candidates = scraper.fetch(SINCE)
    for c in candidates:
        assert c.title.startswith("[Calendar Plus] ")


def test_four_and_five_star_reviews_excluded():
    reviews = load_reviews()
    factory = make_factory({"100": reviews})
    scraper = AppStoreScraper(
        app_ids=[{"id": "100", "name": "Calendar Plus", "slug": "calendar-plus"}],
        scraper_factory=factory,
    )
    candidates = scraper.fetch(SINCE)
    assert all("Love it" not in c.title for c in candidates)


def test_date_filter_excludes_old_reviews():
    reviews = load_reviews()
    factory = make_factory({"100": reviews})
    scraper = AppStoreScraper(
        app_ids=[{"id": "100", "name": "Calendar Plus", "slug": "calendar-plus"}],
        scraper_factory=factory,
    )
    candidates = scraper.fetch(SINCE)
    assert all("Used to be great" not in c.title for c in candidates)


def test_missing_reviewer_name_becomes_deleted():
    reviews = load_reviews()
    factory = make_factory({"100": reviews})
    scraper = AppStoreScraper(
        app_ids=[{"id": "100", "name": "Calendar Plus", "slug": "calendar-plus"}],
        scraper_factory=factory,
    )
    candidates = scraper.fetch(SINCE)
    anon = [c for c in candidates if "Anon feedback" in c.title]
    assert len(anon) == 1
    assert anon[0].author_id == "deleted"


def test_per_app_exception_logged_and_others_continue(caplog):
    reviews = load_reviews()
    factory = make_factory({"good": reviews}, fail_ids={"bad"})
    scraper = AppStoreScraper(
        app_ids=[
            {"id": "bad", "name": "Broken App", "slug": "broken"},
            {"id": "good", "name": "Calendar Plus", "slug": "calendar-plus"},
        ],
        scraper_factory=factory,
    )
    with caplog.at_level(logging.ERROR):
        candidates = scraper.fetch(SINCE)
    assert len(candidates) >= 1
    assert all("[Calendar Plus]" in c.title for c in candidates)
    assert any("bad" in rec.message or "Broken App" in rec.message for rec in caplog.records)


def test_empty_app_ids_yields_nothing():
    scraper = AppStoreScraper(app_ids=[], scraper_factory=make_factory({}))
    assert scraper.fetch(SINCE) == []


def test_review_after_kwarg_passed_to_library():
    reviews = load_reviews()
    stub = AppStoreStub(reviews)

    def factory(country, app_name, app_id):
        return stub

    scraper = AppStoreScraper(
        app_ids=[{"id": "100", "name": "Calendar Plus", "slug": "calendar-plus"}],
        scraper_factory=factory,
        how_many=42,
    )
    scraper.fetch(SINCE)
    assert stub.review_calls
    assert stub.review_calls[0]["after"] == SINCE
    assert stub.review_calls[0]["how_many"] == 42
