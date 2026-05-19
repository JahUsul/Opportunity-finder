"""Apple App Store review scraper — uses `app-store-scraper`.

See design doc §5.1. Title is prefixed with ``[{app_name}]`` so that two reviews
with the same headline across different apps produce distinct dedup hashes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Iterable
from uuid import uuid4

from ..models import Candidate

log = logging.getLogger(__name__)

DEFAULT_RATINGS: tuple[int, ...] = (1, 2, 3)


class AppStoreScraper:
    name = "app_store"

    def __init__(
        self,
        *,
        app_ids: Iterable[dict],
        ratings: Iterable[int] = DEFAULT_RATINGS,
        country: str = "us",
        how_many: int = 100,
        scraper_factory: Callable[..., Any] | None = None,
        **_extra: Any,
    ) -> None:
        self._app_ids = [_normalize_entry(e) for e in app_ids]
        self._ratings = set(int(r) for r in ratings)
        self._country = country
        self._how_many = int(how_many)
        self._scraper_factory = scraper_factory

    def fetch(self, since: datetime) -> list[Candidate]:
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        factory = self._scraper_factory or _default_factory()
        candidates: list[Candidate] = []
        for entry in self._app_ids:
            try:
                candidates.extend(self._fetch_app(entry, factory, since))
            except Exception:
                log.exception(
                    "app_store app %s (%s) failed; continuing",
                    entry.get("id"),
                    entry.get("name"),
                )
        return candidates

    def _fetch_app(
        self,
        entry: dict,
        factory: Callable[..., Any],
        since: datetime,
    ) -> Iterable[Candidate]:
        app = factory(
            country=self._country,
            app_name=entry["slug"],
            app_id=entry["id"],
        )
        app.review(how_many=self._how_many, after=since)
        for review in getattr(app, "reviews", []) or []:
            if review.get("rating") not in self._ratings:
                continue
            review_date = _parse_date(review.get("date"))
            if review_date is not None and review_date < since:
                continue
            yield self._to_candidate(entry, review)

    def _to_candidate(self, entry: dict, review: dict) -> Candidate:
        app_name = entry["name"]
        review_title = (review.get("title") or "").strip()
        body = (review.get("review") or "").strip()
        username = review.get("userName") or ""
        return Candidate(
            id=str(uuid4()),
            source=self.name,
            source_url=_app_url(entry),
            author_id=username.strip() or "deleted",
            title=f"[{app_name}] {review_title}",
            body=body,
            raw_excerpt=body[:500],
            scraped_at=datetime.now(timezone.utc),
        )


def _normalize_entry(entry: dict | str | int) -> dict:
    if isinstance(entry, dict):
        out = {"id": str(entry["id"]), "name": entry.get("name", "")}
        out["slug"] = entry.get("slug") or _slug_fallback(out["name"])
        return out
    raise TypeError(
        f"app_store app_ids entries must be dicts with id/name/slug, got {type(entry).__name__}"
    )


def _slug_fallback(name: str) -> str:
    return name.lower().replace(" ", "-")


def _app_url(entry: dict) -> str:
    slug = entry.get("slug") or ""
    return f"https://apps.apple.com/us/app/{slug}/id{entry['id']}"


def _parse_date(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def _default_factory() -> Callable[..., Any]:
    def make(country: str, app_name: str, app_id: str) -> Any:
        from app_store_scraper import AppStore

        return AppStore(country=country, app_name=app_name, app_id=int(app_id))

    return make
