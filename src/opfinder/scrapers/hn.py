"""Hacker News scraper — Firebase API, see design doc §5.1."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx

from ..models import Candidate

log = logging.getLogger(__name__)

_BASE_URL = "https://hacker-news.firebaseio.com/v0"
_BODY_SEP = "\n\n---\n\n"
_QUERY_ENDPOINTS = {
    "ask_hn": "askstories.json",
    "show_hn": "showstories.json",
}
_TOP_LEVEL_COMMENT_CAP = 10


class HNScraper:
    name = "hn"

    @classmethod
    def is_configured(cls, source_cfg: dict | None = None) -> bool:
        cfg = source_cfg or {}
        return bool(cfg.get("enabled", True))

    def __init__(
        self,
        *,
        query_types: tuple[str, ...] = ("ask_hn", "show_hn"),
        rate_limit_delay: float = 0.1,
        transport: httpx.BaseTransport | None = None,
        request_timeout: float = 30.0,
        **_extra: Any,
    ) -> None:
        unknown = [q for q in query_types if q not in _QUERY_ENDPOINTS]
        if unknown:
            raise ValueError(f"unknown hn query_types: {unknown}")
        self._query_types = tuple(query_types)
        self._rate_limit_delay = float(rate_limit_delay)
        self._transport = transport
        self._request_timeout = float(request_timeout)

    def fetch(self, since: datetime) -> list[Candidate]:
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        return asyncio.run(self._fetch_async(since))

    async def _fetch_async(self, since: datetime) -> list[Candidate]:
        client_kwargs: dict[str, Any] = {"timeout": self._request_timeout}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport
        async with httpx.AsyncClient(**client_kwargs) as client:
            return await self._collect(client, since)

    async def _collect(
        self, client: httpx.AsyncClient, since: datetime
    ) -> list[Candidate]:
        since_ts = since.timestamp()
        candidates: list[Candidate] = []
        for qtype in self._query_types:
            endpoint = f"{_BASE_URL}/{_QUERY_ENDPOINTS[qtype]}"
            ids = await self._get_json(client, endpoint) or []
            for item_id in ids:
                item = await self._get_json(client, f"{_BASE_URL}/item/{item_id}.json")
                if not item or item.get("deleted") or item.get("dead"):
                    continue
                if item.get("type") != "story":
                    continue
                if (item.get("time") or 0) < since_ts:
                    continue
                comment_text = await self._fetch_top_level_comments(
                    client, item.get("kids", []) or []
                )
                candidates.append(self._to_candidate(item, comment_text))
        return candidates

    async def _fetch_top_level_comments(
        self, client: httpx.AsyncClient, kids: list[int]
    ) -> str:
        parts: list[str] = []
        for kid_id in kids[:_TOP_LEVEL_COMMENT_CAP]:
            comment = await self._get_json(client, f"{_BASE_URL}/item/{kid_id}.json")
            if not comment or comment.get("deleted") or comment.get("dead"):
                continue
            text = (comment.get("text") or "").strip()
            if text:
                parts.append(text)
        return _BODY_SEP.join(parts)

    async def _get_json(self, client: httpx.AsyncClient, url: str) -> Any:
        if self._rate_limit_delay > 0:
            await asyncio.sleep(self._rate_limit_delay)
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()

    def _to_candidate(self, item: dict, comment_text: str) -> Candidate:
        item_id = item["id"]
        text = (item.get("text") or "").strip()
        body_parts = [p for p in (text, comment_text) if p]
        body = _BODY_SEP.join(body_parts)
        return Candidate(
            id=str(uuid4()),
            source=self.name,
            source_url=f"https://news.ycombinator.com/item?id={item_id}",
            author_id=item.get("by") or "deleted",
            title=item.get("title") or "",
            body=body,
            raw_excerpt=body[:500],
            scraped_at=datetime.now(timezone.utc),
        )
