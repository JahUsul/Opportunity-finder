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


def make_factory(reviews_by_id: dict[str, list[dict]], *, fail_ids: set[str] = frozenset()):
    """Mimic `AppStoreEntry(app_id, country).reviews(limit=N)`.

    The real factory returns an iterator of `AppReview` dataclass instances;
    our test factory returns a list of dicts. The scraper's `_get(obj, attr)`
    helper handles both shapes.
    """
    calls: list[tuple[str, str]] = []

    def factory(app_id: str, country: str):
        calls.append((str(app_id), country))
        if str(app_id) in fail_ids:
            raise RuntimeError("apple says no")
        return iter(reviews_by_id.get(str(app_id), []))

    factory.calls = calls  # type: ignore[attr-defined]
    return factory


NOW = datetime(2026, 5, 18, tzinfo=timezone.utc)
SINCE = NOW - timedelta(days=7)


def _build_scraper(*, app_ids, factory, **kwargs):
    return AppStoreScraper(
        app_ids=app_ids,
        entry_factory=factory,
        # Loosen the per-app cap so existing fixture coverage isn't truncated.
        reviews_per_app=kwargs.pop("reviews_per_app", 100),
        **kwargs,
    )


def test_happy_path_from_canned_fixture():
    reviews = load_reviews()
    factory = make_factory({"100": reviews})
    scraper = _build_scraper(
        app_ids=[{"id": "100", "name": "Calendar Plus"}],
        factory=factory,
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
    assert sample.source_url == "https://apps.apple.com/us/app/id100"
    assert sample.author_id == "frustrated_pm"
    assert "Sync to Google is unreliable" in sample.body
    assert sample.raw_excerpt == sample.body[:500]


def test_title_is_prefixed_with_app_name():
    factory = make_factory({"100": load_reviews()})
    scraper = _build_scraper(
        app_ids=[{"id": "100", "name": "Calendar Plus"}],
        factory=factory,
    )
    for c in scraper.fetch(SINCE):
        assert c.title.startswith("[Calendar Plus] ")


def test_four_and_five_star_reviews_excluded():
    factory = make_factory({"100": load_reviews()})
    scraper = _build_scraper(
        app_ids=[{"id": "100", "name": "Calendar Plus"}],
        factory=factory,
    )
    assert all("Love it" not in c.title for c in scraper.fetch(SINCE))


def test_date_filter_excludes_old_reviews():
    factory = make_factory({"100": load_reviews()})
    scraper = _build_scraper(
        app_ids=[{"id": "100", "name": "Calendar Plus"}],
        factory=factory,
    )
    assert all("Used to be great" not in c.title for c in scraper.fetch(SINCE))


def test_missing_reviewer_name_becomes_deleted():
    factory = make_factory({"100": load_reviews()})
    scraper = _build_scraper(
        app_ids=[{"id": "100", "name": "Calendar Plus"}],
        factory=factory,
    )
    anon = [c for c in scraper.fetch(SINCE) if "Anon feedback" in c.title]
    assert len(anon) == 1
    assert anon[0].author_id == "deleted"


def test_per_app_exception_logged_and_others_continue(caplog):
    factory = make_factory({"good": load_reviews()}, fail_ids={"bad"})
    scraper = _build_scraper(
        app_ids=[
            {"id": "bad", "name": "Broken App"},
            {"id": "good", "name": "Calendar Plus"},
        ],
        factory=factory,
    )
    with caplog.at_level(logging.ERROR):
        candidates = scraper.fetch(SINCE)
    assert len(candidates) >= 1
    assert all("[Calendar Plus]" in c.title for c in candidates)
    assert any("bad" in rec.message or "Broken App" in rec.message for rec in caplog.records)


def test_empty_app_ids_yields_nothing():
    factory = make_factory({})
    scraper = _build_scraper(app_ids=[], factory=factory)
    assert scraper.fetch(SINCE) == []


def test_factory_receives_app_id_and_country():
    factory = make_factory({"100": load_reviews()})
    scraper = _build_scraper(
        app_ids=[{"id": "100", "name": "Calendar Plus"}],
        factory=factory,
        country="us",
    )
    scraper.fetch(SINCE)
    assert factory.calls == [("100", "us")]


def test_reviews_per_app_caps_output_per_app():
    """Hardening from m5 first-run: each app capped at reviews_per_app entries."""
    # All 5 fixture rows are in-window + correct rating for this test, except
    # the 5-star and old one. So before cap we'd get 3; cap=1 trims to 1.
    factory = make_factory({"100": load_reviews()})
    scraper = AppStoreScraper(
        app_ids=[{"id": "100", "name": "Calendar Plus"}],
        entry_factory=factory,
        reviews_per_app=1,
    )
    assert len(scraper.fetch(SINCE)) == 1


def test_reviews_per_app_default_is_five():
    scraper = AppStoreScraper(app_ids=[])
    assert scraper._reviews_per_app == 5
