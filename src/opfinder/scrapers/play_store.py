"""Google Play review scraper — uses `google-play-scraper`.

See design doc §5.1. Play reviews lack a ``title`` field, so the dedup title is
``[{app_name}] {first ~80 chars of content}`` — keeps the hash distinct per
review while staying human-readable.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Iterable
from uuid import uuid4

from ..models import Candidate

log = logging.getLogger(__name__)

DEFAULT_RATINGS: tuple[int, ...] = (1, 2, 3)
_TITLE_SNIPPET_LEN = 80


class PlayStoreScraper:
    name = "play_store"

    def __init__(
        self,
        *,
        app_ids: Iterable[dict],
        ratings: Iterable[int] = DEFAULT_RATINGS,
        country: str = "us",
        lang: str = "en",
        count: int = 100,
        reviews_fn: Callable[..., Any] | None = None,
        sort: Any = None,
        **_extra: Any,
    ) -> None:
        self._app_ids = [_normalize_entry(e) for e in app_ids]
        self._ratings = set(int(r) for r in ratings)
        self._country = country
        self._lang = lang
        self._count = int(count)
        self._reviews_fn = reviews_fn
        self._sort = sort

    def fetch(self, since: datetime) -> list[Candidate]:
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        reviews_fn, sort = self._resolve_lib()
        candidates: list[Candidate] = []
        for entry in self._app_ids:
            try:
                candidates.extend(self._fetch_app(entry, reviews_fn, sort, since))
            except Exception:
                log.exception(
                    "play_store app %s (%s) failed; continuing",
                    entry.get("id"),
                    entry.get("name"),
                )
        return candidates

    def _resolve_lib(self) -> tuple[Callable[..., Any], Any]:
        if self._reviews_fn is not None:
            return self._reviews_fn, self._sort
        from google_play_scraper import Sort, reviews

        return reviews, Sort.NEWEST

    def _fetch_app(
        self,
        entry: dict,
        reviews_fn: Callable[..., Any],
        sort: Any,
        since: datetime,
    ) -> Iterable[Candidate]:
        kwargs: dict[str, Any] = {
            "lang": self._lang,
            "country": self._country,
            "count": self._count,
        }
        if sort is not None:
            kwargs["sort"] = sort
        result = reviews_fn(entry["id"], **kwargs)
        reviews_list = result[0] if isinstance(result, tuple) else result
        for review in reviews_list or []:
            if review.get("score") not in self._ratings:
                continue
            at = _parse_date(review.get("at"))
            if at is not None and at < since:
                continue
            yield self._to_candidate(entry, review)

    def _to_candidate(self, entry: dict, review: dict) -> Candidate:
        app_name = entry["name"]
        content = (review.get("content") or "").strip()
        snippet = _snippet(content)
        username = review.get("userName") or ""
        return Candidate(
            id=str(uuid4()),
            source=self.name,
            source_url=f"https://play.google.com/store/apps/details?id={entry['id']}",
            author_id=username.strip() or "deleted",
            title=f"[{app_name}] {snippet}",
            body=content,
            raw_excerpt=content[:500],
            scraped_at=datetime.now(timezone.utc),
        )


def _snippet(content: str, max_len: int = _TITLE_SNIPPET_LEN) -> str:
    if not content:
        return ""
    first_line = content.splitlines()[0].strip()
    if len(first_line) <= max_len:
        return first_line
    cut = first_line[:max_len].rsplit(" ", 1)[0]
    return f"{cut}..." if cut else first_line[:max_len] + "..."


def _normalize_entry(entry: dict | str) -> dict:
    if isinstance(entry, dict):
        return {"id": str(entry["id"]), "name": entry.get("name", "")}
    raise TypeError(
        f"play_store app_ids entries must be dicts with id/name, got {type(entry).__name__}"
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
