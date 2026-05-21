"""Integration test for opfinder.main.run() — mocks all I/O boundaries."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from opfinder.config import load_config as real_load_config
from opfinder.models import Candidate


def make_candidate(id_: str, source: str, title: str = "t") -> Candidate:
    return Candidate(
        id=id_,
        source=source,
        source_url=f"https://example.com/{id_}",
        author_id="alice",
        title=title,
        body=f"body for {id_}",
        raw_excerpt="excerpt",
        scraped_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
    )


def make_mock_score(score: int):
    """Build a MagicMock Anthropic response with the given numeric score."""
    text_block = MagicMock()
    text_block.text = f'{{"score": {score}, "reasoning": "ok"}}'
    resp = MagicMock()
    resp.content = [text_block]
    resp.usage = MagicMock(input_tokens=10, output_tokens=20)
    return resp


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Configure a stable environment that exercises Reddit-skip + the other three."""
    # Reddit env left as placeholders so is_configured() returns False.
    monkeypatch.setenv("REDDIT_CLIENT_ID", "REPLACE_ME")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "REPLACE_ME")
    monkeypatch.setenv("REDDIT_USER_AGENT", "REPLACE_ME")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_PATH", "dummy_client.json")
    monkeypatch.setenv("GOOGLE_OAUTH_TOKEN_PATH", "dummy_token.json")
    monkeypatch.setenv("SHEET_ID", "TEST_SHEET")
    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "harris.jason121@gmail.com")
    monkeypatch.setenv("SMTP_PASS", "test-app-pass")
    monkeypatch.setenv("NOTIFY_TO", "harris.jason121@gmail.com")

    # Isolate data dir so dedup writes to tmp.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "sources.yaml").write_text(
        "reddit:\n"
        "  subreddits: [automation]\n"
        "  min_score: 5\n"
        "  min_comments: 3\n"
        "hn:\n"
        "  query_types: [ask_hn, show_hn]\n"
        "  lookback_days: 7\n"
        "app_store:\n"
        "  app_ids: []\n"
        "play_store:\n"
        "  app_ids: []\n"
    )
    (tmp_path / "config" / "scoring.yaml").write_text(
        "promotion_threshold: 30\n"
        "lane_oss_cutoff: 3\n"
    )
    return tmp_path


def test_main_run_end_to_end(env):
    hn_candidates = [make_candidate("hn-1", "hn", "Ask HN: best CRM?"),
                     make_candidate("hn-2", "hn", "Show HN: launched a thing")]
    app_candidates = [make_candidate("app-1", "app_store", "[App] Crashes")]
    play_candidates = [make_candidate("play-1", "play_store", "[App] Sync broken")]

    # Anthropic mock — every call returns score=6.
    async def fake_create(**kwargs):
        return make_mock_score(6)

    mock_anthropic_client = MagicMock()
    mock_anthropic_client.messages = MagicMock()
    mock_anthropic_client.messages.create = fake_create

    # Mock SheetWriter and Notifier at the import points in opfinder.main.
    sheet_writer_instance = MagicMock()
    sheet_writer_instance.write_week.return_value = "https://docs.google.com/spreadsheets/d/TEST_SHEET/edit#gid=99"
    sheet_writer_cls = MagicMock(return_value=sheet_writer_instance)

    notifier_instance = MagicMock()
    notifier_cls = MagicMock(return_value=notifier_instance)

    # Mock scrapers — Reddit will be skipped by is_configured(), not called.
    reddit_cls = MagicMock()
    reddit_cls.name = "reddit"
    reddit_cls.is_configured.return_value = False

    hn_instance = MagicMock()
    hn_instance.fetch.return_value = hn_candidates
    hn_cls = MagicMock(return_value=hn_instance)
    hn_cls.name = "hn"
    hn_cls.is_configured.return_value = True

    app_instance = MagicMock()
    app_instance.fetch.return_value = app_candidates
    app_cls = MagicMock(return_value=app_instance)
    app_cls.name = "app_store"
    app_cls.is_configured.return_value = True

    play_instance = MagicMock()
    play_instance.fetch.return_value = play_candidates
    play_cls = MagicMock(return_value=play_instance)
    play_cls.name = "play_store"
    play_cls.is_configured.return_value = True

    with patch("opfinder.main.RedditScraper", reddit_cls), \
         patch("opfinder.main.HNScraper", hn_cls), \
         patch("opfinder.main.AppStoreScraper", app_cls), \
         patch("opfinder.main.PlayStoreScraper", play_cls), \
         patch("opfinder.main.SheetWriter", sheet_writer_cls), \
         patch("opfinder.main.Notifier", notifier_cls), \
         patch("opfinder.main.load_config", lambda: real_load_config(project_root=env)), \
         patch("opfinder.scorer.anthropic.AsyncAnthropic", return_value=mock_anthropic_client):
        from opfinder.main import run
        run()

    # Reddit: not constructed, not fetched, because is_configured() is False.
    reddit_cls.is_configured.assert_called()
    reddit_cls.assert_not_called()  # constructor never invoked

    # HN / App Store / Play Store: each fetched exactly once.
    hn_instance.fetch.assert_called_once()
    app_instance.fetch.assert_called_once()
    play_instance.fetch.assert_called_once()

    # SheetWriter: called once with 4 candidates (2 HN + 1 each).
    sheet_writer_instance.write_week.assert_called_once()
    args, kwargs = sheet_writer_instance.write_week.call_args
    written_candidates = args[0]
    assert len(written_candidates) == 4

    # Sorted desc by machine_total — all scored to 6+6+6+0 = 18, so all equal.
    # Just check they're all scored.
    for c in written_candidates:
        assert c.pain == 6 and c.money == 6 and c.buyer == 6
        assert c.oss == 0
        assert c.lane == "greenfield"
        assert c.machine_total == 18

    # Notifier: ready email (no failures), not partial.
    notifier_instance.send_ready_email.assert_called_once()
    notifier_instance.send_partial_email.assert_not_called()
    args, kwargs = notifier_instance.send_ready_email.call_args
    sheet_url = args[0]
    stats = args[1]
    assert sheet_url == "https://docs.google.com/spreadsheets/d/TEST_SHEET/edit#gid=99"
    assert stats["n_candidates"] == 4
    assert stats["n_greenfield"] == 4
    assert stats["n_fast"] == 0


def test_main_run_falls_back_to_partial_email_when_a_scraper_fails(env):
    hn_candidates = [make_candidate("hn-1", "hn")]

    async def fake_create(**kwargs):
        return make_mock_score(5)

    mock_anthropic_client = MagicMock()
    mock_anthropic_client.messages = MagicMock()
    mock_anthropic_client.messages.create = fake_create

    sheet_writer_instance = MagicMock()
    sheet_writer_instance.write_week.return_value = "https://x/y#gid=1"
    sheet_writer_cls = MagicMock(return_value=sheet_writer_instance)

    notifier_instance = MagicMock()
    notifier_cls = MagicMock(return_value=notifier_instance)

    reddit_cls = MagicMock()
    reddit_cls.name = "reddit"
    reddit_cls.is_configured.return_value = False

    hn_instance = MagicMock()
    hn_instance.fetch.return_value = hn_candidates
    hn_cls = MagicMock(return_value=hn_instance)
    hn_cls.name = "hn"
    hn_cls.is_configured.return_value = True

    # App Store fails mid-fetch.
    app_instance = MagicMock()
    app_instance.fetch.side_effect = RuntimeError("api down")
    app_cls = MagicMock(return_value=app_instance)
    app_cls.name = "app_store"
    app_cls.is_configured.return_value = True

    play_instance = MagicMock()
    play_instance.fetch.return_value = []
    play_cls = MagicMock(return_value=play_instance)
    play_cls.name = "play_store"
    play_cls.is_configured.return_value = True

    with patch("opfinder.main.RedditScraper", reddit_cls), \
         patch("opfinder.main.HNScraper", hn_cls), \
         patch("opfinder.main.AppStoreScraper", app_cls), \
         patch("opfinder.main.PlayStoreScraper", play_cls), \
         patch("opfinder.main.SheetWriter", sheet_writer_cls), \
         patch("opfinder.main.Notifier", notifier_cls), \
         patch("opfinder.main.load_config", lambda: real_load_config(project_root=env)), \
         patch("opfinder.scorer.anthropic.AsyncAnthropic", return_value=mock_anthropic_client):
        from opfinder.main import run
        run()

    notifier_instance.send_partial_email.assert_called_once()
    notifier_instance.send_ready_email.assert_not_called()
    failures = notifier_instance.send_partial_email.call_args[0][1]
    assert any(f["source"] == "app_store" for f in failures)
