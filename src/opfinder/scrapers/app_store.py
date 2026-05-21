"""Apple App Store review scraper — uses `app-store-web-scraper`.

Swapped from the archived `app-store-scraper` on 2026-05-20 after the
first live run returned zero candidates. See docs/supply-chain-vets/m3.md
for the swap rationale. Title is prefixed with ``[{app_name}]`` so two
reviews with the same headline across different apps produce distinct
dedup hashes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Iterator
from uuid import uuid4

from ..models import Candidate

log = logging.getLogger(__name__)

DEFAULT_RATINGS: tuple[int, ...] = (1, 2, 3)


class AppStoreScraper:
    name = "app_store"

    @classmethod
    def is_configured(cls, source_cfg: dict | None = None) -> bool:
        cfg = source_cfg or {}
        return bool(cfg.get("enabled", True))

    def __init__(
        self,
        *,
        app_ids: Iterable[dict],
        ratings: Iterable[int] = DEFAULT_RATINGS,
        country: str = "us",
        reviews_per_app: int = 5,
        page_limit: int = 100,
        entry_factory: Callable[[str, str], Iterable[Any]] | None = None,
        **_extra: Any,
    ) -> None:
        self._app_ids = [_normalize_entry(e) for e in app_ids]
        self._ratings = set(int(r) for r in ratings)
        self._country = country
        self._reviews_per_app = int(reviews_per_app)
        self._page_limit = int(page_limit)
        self._entry_factory = entry_factory

    def fetch(self, since: datetime) -> list[Candidate]:
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        factory = self._entry_factory or self._default_factory()
        candidates: list[Candidate] = []
        for entry in self._app_ids:
            try:
                candidates.extend(self._fetch_app(entry, factory, since))
            except Exception:
                log.exception(
                    "app_store app %s (%s) failed; continuing",
                    entry.get("id"), entry.get("name"),
                )
        return candidates

    def _default_factory(self) -> Callable[[str, str], Iterator[Any]]:
        page_limit = self._page_limit

        def make(app_id: str, country: str) -> Iterator[Any]:
            from app_store_web_scraper import AppStoreEntry, AppStoreSession
            session = AppStoreSession(delay=0.5, retries=5)
            entry = AppStoreEntry(app_id=app_id, country=country, session=session)
            return entry.reviews(limit=page_limit)

        return make

    def _fetch_app(
        self,
        entry: dict,
        factory: Callable[[str, str], Iterable[Any]],
        since: datetime,
    ) -> Iterable[Candidate]:
        kept = 0
        for review in factory(entry["id"], self._country):
            if _get(review, "rating") not in self._ratings:
                continue
            review_date = _parse_date(_get(review, "date"))
            if review_date is not None and review_date < since:
                continue
            yield self._to_candidate(entry, review)
            kept += 1
            if kept >= self._reviews_per_app:
                break

    def _to_candidate(self, entry: dict, review: Any) -> Candidate:
        app_name = entry["name"]
        review_title = (_get(review, "title") or "").strip()
        body = (_get(review, "content") or "").strip()
        username = _get(review, "user_name") or ""
        return Candidate(
            id=str(uuid4()),
            source=self.name,
            source_url=f"https://apps.apple.com/{self._country}/app/id{entry['id']}",
            author_id=str(username).strip() or "deleted",
            title=f"[{app_name}] {review_title}",
            body=body,
            raw_excerpt=body[:500],
            scraped_at=datetime.now(timezone.utc),
        )


def _get(obj: Any, attr: str) -> Any:
    """Read attribute from dataclass instance or dict — same call site, either shape."""
    if isinstance(obj, dict):
        return obj.get(attr)
    return getattr(obj, attr, None)


def _normalize_entry(entry: dict | str | int) -> dict:
    if isinstance(entry, dict):
        return {"id": str(entry["id"]), "name": entry.get("name", "")}
    raise TypeError(
        f"app_store app_ids entries must be dicts with id/name, got {type(entry).__name__}"
    )


def _parse_date(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None
