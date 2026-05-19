import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable

import pytest

from opfinder.scrapers.reddit import RedditScraper


# Minimal PRAW shape used by the scraper.


class FakeAuthor:
    def __init__(self, name: str | None) -> None:
        if name is None:
            raise ValueError("use author=None on the submission for a deleted user")
        self.name = name


class FakeComment:
    def __init__(self, body: str, score: int = 0) -> None:
        self.body = body
        self.score = score


class FakeCommentForest:
    def __init__(self, comments: Iterable[FakeComment]) -> None:
        self._comments = list(comments)
        self.replace_more_calls = 0

    def replace_more(self, limit: int = 0) -> list:
        self.replace_more_calls += 1
        return []

    def __iter__(self):
        return iter(self._comments)


class FakeSubmission:
    def __init__(
        self,
        *,
        title: str,
        score: int,
        num_comments: int,
        created_utc: float,
        selftext: str = "",
        author: FakeAuthor | None = None,
        permalink: str = "/r/test/comments/abc/title/",
        comments: Iterable[FakeComment] = (),
    ) -> None:
        self.title = title
        self.score = score
        self.num_comments = num_comments
        self.created_utc = created_utc
        self.selftext = selftext
        self.author = author
        self.permalink = permalink
        self.comments = FakeCommentForest(comments)


class FakeSubreddit:
    def __init__(
        self,
        submissions: Iterable[FakeSubmission] = (),
        *,
        raises: Exception | None = None,
    ) -> None:
        self._submissions = list(submissions)
        self._raises = raises
        self.new_calls: list[int | None] = []

    def new(self, limit: int | None = None):
        self.new_calls.append(limit)
        if self._raises is not None:
            raise self._raises
        items = self._submissions if limit is None else self._submissions[:limit]
        return iter(items)


class FakeReddit:
    def __init__(self, subs: dict[str, FakeSubreddit]) -> None:
        self._subs = subs

    def subreddit(self, name: str) -> FakeSubreddit:
        return self._subs[name]


# Helpers.


NOW = datetime(2026, 5, 18, tzinfo=timezone.utc)
SINCE = NOW - timedelta(days=7)


def ts(dt: datetime) -> float:
    return dt.timestamp()


def make_submission(**overrides) -> FakeSubmission:
    defaults = dict(
        title="Stripe webhooks are eating my Saturdays",
        score=12,
        num_comments=5,
        created_utc=ts(NOW - timedelta(days=1)),
        selftext="we lose ~6 hours/week reconciling",
        author=FakeAuthor("alice"),
        comments=[FakeComment("we use Zapier; same problem", score=8)],
    )
    defaults.update(overrides)
    return FakeSubmission(**defaults)


# Tests.


def test_happy_path_returns_candidates_with_full_fields():
    sub = FakeSubreddit(
        [
            make_submission(
                title="Stripe webhooks",
                selftext="painful",
                comments=[
                    FakeComment("low-vote comment", score=1),
                    FakeComment("top comment", score=100),
                    FakeComment("mid comment", score=10),
                    FakeComment("another", score=5),
                ],
            ),
        ]
    )
    scraper = RedditScraper(FakeReddit({"automation": sub}), subreddits=["automation"])
    [c] = scraper.fetch(SINCE)
    assert c.source == "reddit"
    assert c.title == "Stripe webhooks"
    assert c.author_id == "alice"
    assert c.source_url.startswith("https://reddit.com/r/")
    # Body has selftext + top 3 comments by score, with separator.
    assert "painful" in c.body
    assert "top comment" in c.body
    assert "mid comment" in c.body
    assert "another" in c.body
    assert "low-vote comment" not in c.body  # only top 3 by score
    assert "\n\n---\n\n" in c.body
    assert c.raw_excerpt == c.body[:500]


def test_deleted_author_becomes_literal_deleted():
    sub = FakeSubreddit([make_submission(author=None)])
    scraper = RedditScraper(FakeReddit({"automation": sub}), subreddits=["automation"])
    [c] = scraper.fetch(SINCE)
    assert c.author_id == "deleted"


def test_empty_subreddit_returns_nothing():
    sub = FakeSubreddit([])
    scraper = RedditScraper(FakeReddit({"automation": sub}), subreddits=["automation"])
    assert scraper.fetch(SINCE) == []


def test_post_below_comment_threshold_is_filtered_out():
    sub = FakeSubreddit([make_submission(num_comments=1)])
    scraper = RedditScraper(
        FakeReddit({"automation": sub}),
        subreddits=["automation"],
        min_comments=3,
    )
    assert scraper.fetch(SINCE) == []


def test_post_below_score_threshold_is_filtered_out():
    sub = FakeSubreddit([make_submission(score=2)])
    scraper = RedditScraper(
        FakeReddit({"automation": sub}),
        subreddits=["automation"],
        min_score=5,
    )
    assert scraper.fetch(SINCE) == []


def test_old_post_is_filtered_out():
    old = make_submission(created_utc=ts(NOW - timedelta(days=30)))
    fresh = make_submission(title="Fresh post")
    sub = FakeSubreddit([old, fresh])
    scraper = RedditScraper(FakeReddit({"automation": sub}), subreddits=["automation"])
    [c] = scraper.fetch(SINCE)
    assert c.title == "Fresh post"


def test_subreddit_exception_is_logged_and_others_continue(caplog):
    boom = FakeSubreddit(raises=RuntimeError("403"))
    ok = FakeSubreddit([make_submission(title="ok post")])
    scraper = RedditScraper(
        FakeReddit({"blowsup": boom, "automation": ok}),
        subreddits=["blowsup", "automation"],
    )
    with caplog.at_level(logging.ERROR):
        candidates = scraper.fetch(SINCE)
    assert len(candidates) == 1
    assert candidates[0].title == "ok post"
    assert any("blowsup" in rec.message for rec in caplog.records)


def test_replace_more_is_called_on_comments():
    s = make_submission()
    sub = FakeSubreddit([s])
    scraper = RedditScraper(FakeReddit({"automation": sub}), subreddits=["automation"])
    scraper.fetch(SINCE)
    assert s.comments.replace_more_calls == 1


def test_naive_since_is_treated_as_utc():
    sub = FakeSubreddit([make_submission()])
    scraper = RedditScraper(FakeReddit({"automation": sub}), subreddits=["automation"])
    naive_since = (NOW - timedelta(days=7)).replace(tzinfo=None)
    assert len(scraper.fetch(naive_since)) == 1
