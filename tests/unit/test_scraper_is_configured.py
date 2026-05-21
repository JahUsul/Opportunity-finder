import pytest

from opfinder.scrapers.app_store import AppStoreScraper
from opfinder.scrapers.hn import HNScraper
from opfinder.scrapers.play_store import PlayStoreScraper
from opfinder.scrapers.reddit import RedditScraper


# ----- Reddit -----


REDDIT_KEYS = ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT")


def _set_reddit_env(monkeypatch, **overrides):
    defaults = {
        "REDDIT_CLIENT_ID": "real-client-id",
        "REDDIT_CLIENT_SECRET": "real-secret",
        "REDDIT_USER_AGENT": "opfinder/0.1 by /u/jasonharris",
    }
    defaults.update(overrides)
    for key, value in defaults.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def test_reddit_is_configured_with_all_creds_present(monkeypatch):
    _set_reddit_env(monkeypatch)
    assert RedditScraper.is_configured({"enabled": True}) is True


@pytest.mark.parametrize("missing", REDDIT_KEYS)
def test_reddit_not_configured_when_any_var_missing(monkeypatch, missing):
    _set_reddit_env(monkeypatch, **{missing: None})
    assert RedditScraper.is_configured({"enabled": True}) is False


@pytest.mark.parametrize("missing", REDDIT_KEYS)
def test_reddit_not_configured_when_any_var_empty(monkeypatch, missing):
    _set_reddit_env(monkeypatch, **{missing: ""})
    assert RedditScraper.is_configured({"enabled": True}) is False


@pytest.mark.parametrize(
    "placeholder",
    ["REPLACE_ME", "replace_me", "REPLACE_ME_PLEASE", "<TODO>"],
)
def test_reddit_not_configured_with_placeholder(monkeypatch, placeholder):
    _set_reddit_env(monkeypatch, REDDIT_CLIENT_ID=placeholder)
    assert RedditScraper.is_configured({"enabled": True}) is False


def test_reddit_not_configured_when_only_one_placeholder(monkeypatch):
    _set_reddit_env(monkeypatch, REDDIT_CLIENT_SECRET="REPLACE_ME")
    assert RedditScraper.is_configured({"enabled": True}) is False


def test_reddit_not_configured_when_disabled(monkeypatch):
    _set_reddit_env(monkeypatch)
    assert RedditScraper.is_configured({"enabled": False}) is False


# ----- enabled flag governs HN, App Store, Play Store -----


def test_hn_configured_when_enabled():
    assert HNScraper.is_configured({"enabled": True}) is True


def test_hn_not_configured_when_disabled():
    assert HNScraper.is_configured({"enabled": False}) is False


def test_hn_defaults_to_enabled_when_flag_missing():
    assert HNScraper.is_configured({}) is True
    assert HNScraper.is_configured() is True


def test_app_store_not_configured_when_disabled():
    """v0 default — see config/sources.yaml rationale."""
    assert AppStoreScraper.is_configured({"enabled": False}) is False


def test_app_store_configured_when_enabled():
    assert AppStoreScraper.is_configured({"enabled": True}) is True


def test_play_store_not_configured_when_disabled():
    """v0 default — see config/sources.yaml rationale."""
    assert PlayStoreScraper.is_configured({"enabled": False}) is False


def test_play_store_configured_when_enabled():
    assert PlayStoreScraper.is_configured({"enabled": True}) is True


def test_callable_on_instance_too():
    """Protocol shape: is_configured callable on instances as well as classes."""
    assert HNScraper().is_configured({"enabled": True}) is True
    assert AppStoreScraper(app_ids=[]).is_configured({"enabled": True}) is True
    assert PlayStoreScraper(app_ids=[]).is_configured({"enabled": True}) is True
