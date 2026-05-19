"""Reddit scraper — PRAW-based, see design doc §5.1."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import uuid4

from ..models import Candidate

log = logging.getLogger(__name__)

_BODY_SEP = "\n\n---\n\n"


class RedditScraper:
    name = "reddit"

    def __init__(
        self,
        reddit_client: Any,
        *,
        subreddits: Iterable[str],
        min_score: int = 5,
        min_comments: int = 3,
        new_limit: int = 100,
        **_extra: Any,
    ) -> None:
        self._reddit = reddit_client
        self._subreddits = list(subreddits)
        self._min_score = int(min_score)
        self._min_comments = int(min_comments)
        self._new_limit = int(new_limit)

    @classmethod
    def from_env(cls, sources_reddit: dict, **client_overrides: Any) -> "RedditScraper":
        import praw

        client = praw.Reddit(
            client_id=os.environ["REDDIT_CLIENT_ID"],
            client_secret=os.environ["REDDIT_CLIENT_SECRET"],
            user_agent=os.environ.get("REDDIT_USER_AGENT", "opfinder/0.1"),
            **client_overrides,
        )
        return cls(client, **sources_reddit)

    def fetch(self, since: datetime) -> list[Candidate]:
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        candidates: list[Candidate] = []
        for sub_name in self._subreddits:
            try:
                candidates.extend(self._fetch_subreddit(sub_name, since))
            except Exception:
                log.exception("reddit subreddit %s failed; continuing", sub_name)
        return candidates

    def _fetch_subreddit(self, sub_name: str, since: datetime) -> Iterable[Candidate]:
        sub = self._reddit.subreddit(sub_name)
        for post in sub.new(limit=self._new_limit):
            if post.score < self._min_score:
                continue
            if post.num_comments < self._min_comments:
                continue
            created = datetime.fromtimestamp(post.created_utc, tz=timezone.utc)
            if created < since:
                continue
            yield self._to_candidate(post)

    def _to_candidate(self, post: Any) -> Candidate:
        body = self._build_body(post)
        permalink = getattr(post, "permalink", "") or ""
        source_url = (
            permalink if permalink.startswith("http") else f"https://reddit.com{permalink}"
        )
        return Candidate(
            id=str(uuid4()),
            source=self.name,
            source_url=source_url,
            author_id=_author_name(post),
            title=post.title,
            body=body,
            raw_excerpt=body[:500],
            scraped_at=datetime.now(timezone.utc),
        )

    def _build_body(self, post: Any) -> str:
        parts: list[str] = []
        selftext = (getattr(post, "selftext", "") or "").strip()
        if selftext:
            parts.append(selftext)
        for comment in _top_comments(post, n=3):
            text = (getattr(comment, "body", "") or "").strip()
            if text:
                parts.append(text)
        return _BODY_SEP.join(parts)


def _author_name(post: Any) -> str:
    author = getattr(post, "author", None)
    if author is None:
        return "deleted"
    try:
        name = author.name
    except AttributeError:
        return "deleted"
    return name or "deleted"


def _top_comments(post: Any, *, n: int) -> list[Any]:
    forest = getattr(post, "comments", None)
    if forest is None:
        return []
    try:
        forest.replace_more(limit=0)
    except AttributeError:
        pass
    try:
        top_level = list(forest)
    except TypeError:
        return []
    return sorted(top_level, key=lambda c: getattr(c, "score", 0) or 0, reverse=True)[:n]
