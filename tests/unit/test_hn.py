import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from opfinder.scrapers.hn import HNScraper

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hn"


def _route(routes: dict, default_status: int = 404):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        for needle, payload in routes.items():
            if needle in path:
                if isinstance(payload, Exception):
                    raise payload
                return httpx.Response(200, json=payload)
        return httpx.Response(default_status)

    return handler


def transport(routes: dict) -> httpx.MockTransport:
    return httpx.MockTransport(_route(routes))


def from_sample() -> dict:
    return json.loads((FIXTURES / "sample.json").read_text())


# `time` value used inside sample.json corresponds to this UTC moment.
SAMPLE_NOW = datetime.fromtimestamp(1779408000, tz=timezone.utc)
SINCE = SAMPLE_NOW - timedelta(days=2)


def test_happy_path_from_canned_fixture():
    sample = from_sample()
    routes = {
        "/askstories.json": sample["askstories"],
        "/showstories.json": sample["showstories"],
    }
    for item_id, body in sample["items"].items():
        routes[f"/item/{item_id}.json"] = body

    scraper = HNScraper(
        rate_limit_delay=0,
        transport=transport(routes),
    )
    candidates = scraper.fetch(SINCE)

    # 2 valid stories: item 100 (Ask) and item 300 (Show).
    # 102 is dead, 103 is too old → filtered.
    assert len(candidates) == 2
    titles = {c.title for c in candidates}
    assert any("solo realtors" in t for t in titles)
    assert any("meeting-notes summarizer" in t for t in titles)

    ask = next(c for c in candidates if "realtors" in c.title)
    assert ask.source == "hn"
    assert ask.author_id == "founder42"
    assert ask.source_url == "https://news.ycombinator.com/item?id=100"
    # Body contains story text and both alive top-level comments, not the deleted one.
    assert "Sheets is buckling" in ask.body
    assert "Pipedrive" in ask.body
    assert "Followup Boss" in ask.body
    assert "\n\n---\n\n" in ask.body
    assert ask.raw_excerpt == ask.body[:500]


def test_dead_and_deleted_stories_are_skipped():
    routes = {
        "/askstories.json": [1, 2],
        "/showstories.json": [],
        "/item/1.json": {"id": 1, "type": "story", "dead": True, "time": SAMPLE_NOW.timestamp()},
        "/item/2.json": {"id": 2, "type": "story", "deleted": True, "time": SAMPLE_NOW.timestamp()},
    }
    scraper = HNScraper(rate_limit_delay=0, transport=transport(routes))
    assert scraper.fetch(SINCE) == []


def test_old_story_is_filtered_by_since():
    too_old = SAMPLE_NOW - timedelta(days=30)
    routes = {
        "/askstories.json": [10],
        "/showstories.json": [],
        "/item/10.json": {
            "id": 10,
            "type": "story",
            "by": "x",
            "title": "old",
            "text": "ancient",
            "time": too_old.timestamp(),
        },
    }
    scraper = HNScraper(rate_limit_delay=0, transport=transport(routes))
    assert scraper.fetch(SINCE) == []


def test_non_story_types_are_filtered():
    routes = {
        "/askstories.json": [20],
        "/showstories.json": [],
        "/item/20.json": {
            "id": 20,
            "type": "poll",
            "by": "x",
            "title": "is this a poll?",
            "time": SAMPLE_NOW.timestamp(),
        },
    }
    scraper = HNScraper(rate_limit_delay=0, transport=transport(routes))
    assert scraper.fetch(SINCE) == []


def test_empty_endpoints_return_no_candidates():
    routes = {"/askstories.json": [], "/showstories.json": []}
    scraper = HNScraper(rate_limit_delay=0, transport=transport(routes))
    assert scraper.fetch(SINCE) == []


def test_unknown_query_type_raises():
    with pytest.raises(ValueError):
        HNScraper(query_types=("ask_hn", "bogus_endpoint"))


def test_http_error_propagates():
    def handler(request):
        return httpx.Response(500)

    scraper = HNScraper(
        rate_limit_delay=0,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(httpx.HTTPStatusError):
        scraper.fetch(SINCE)


def test_missing_author_falls_back_to_deleted():
    routes = {
        "/askstories.json": [30],
        "/showstories.json": [],
        "/item/30.json": {
            "id": 30,
            "type": "story",
            "title": "anon",
            "text": "no author here",
            "time": SAMPLE_NOW.timestamp(),
        },
    }
    scraper = HNScraper(rate_limit_delay=0, transport=transport(routes))
    [c] = scraper.fetch(SINCE)
    assert c.author_id == "deleted"


def test_only_show_hn_when_query_types_restricted():
    sample = from_sample()
    routes = {
        "/showstories.json": sample["showstories"],
    }
    for item_id in sample["showstories"]:
        routes[f"/item/{item_id}.json"] = sample["items"][str(item_id)]

    scraper = HNScraper(
        query_types=("show_hn",),
        rate_limit_delay=0,
        transport=transport(routes),
    )
    candidates = scraper.fetch(SINCE)
    assert len(candidates) == 1
    assert "meeting-notes" in candidates[0].title


def test_naive_since_is_treated_as_utc():
    sample = from_sample()
    routes = {
        "/askstories.json": sample["askstories"],
        "/showstories.json": sample["showstories"],
    }
    for item_id, body in sample["items"].items():
        routes[f"/item/{item_id}.json"] = body

    scraper = HNScraper(rate_limit_delay=0, transport=transport(routes))
    naive = SINCE.replace(tzinfo=None)
    candidates = scraper.fetch(naive)
    assert len(candidates) == 2
