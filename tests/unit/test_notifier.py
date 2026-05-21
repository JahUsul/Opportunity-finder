from unittest.mock import MagicMock

import pytest

from opfinder.notifier import Notifier


@pytest.fixture
def captured():
    """Build a fake smtp_factory; capture the message and login args."""
    state: dict = {"messages": [], "logins": [], "ctx_calls": []}

    def factory(host, port):
        state["host_port"] = (host, port)
        smtp = MagicMock()
        smtp.__enter__ = lambda self: smtp
        smtp.__exit__ = lambda *a, **kw: False
        smtp.login.side_effect = lambda u, p: state["logins"].append((u, p))
        smtp.send_message.side_effect = lambda m: state["messages"].append(m)
        return smtp

    return factory, state


def make_notifier(factory) -> Notifier:
    return Notifier(
        host="smtp.gmail.com",
        port=587,
        user="harris.jason121@gmail.com",
        password="appPasswordHere",
        notify_to="harris.jason121@gmail.com",
        smtp_factory=factory,
    )


def test_send_ready_email_subject_and_body(captured):
    factory, state = captured
    notifier = make_notifier(factory)
    notifier.send_ready_email(
        sheet_url="https://docs.google.com/spreadsheets/d/abc/edit#gid=11",
        stats={
            "date": "2026-05-19",
            "n_candidates": 87,
            "n_fast": 12,
            "n_greenfield": 75,
            "cost": "1.42",
        },
    )
    msg = state["messages"][0]
    assert msg["Subject"] == "Opportunity-Finder ready — week of 2026-05-19"
    assert msg["To"] == "harris.jason121@gmail.com"
    assert msg["From"] == "harris.jason121@gmail.com"
    body = msg.get_content()
    assert "87 candidates scored" in body
    assert "12 in the fast lane" in body
    assert "75 in greenfield" in body
    assert "Cost this run: $1.42" in body
    assert "https://docs.google.com/spreadsheets/d/abc/edit#gid=11" in body


def test_send_partial_email_lists_failures(captured):
    factory, state = captured
    notifier = make_notifier(factory)
    failures = [
        {"source": "reddit", "stage": "scrape", "error": "401 Unauthorized"},
        {"source": "play_store", "stage": "scrape", "error": "HTTP 429 rate limit"},
    ]
    notifier.send_partial_email(
        sheet_url="https://docs.google.com/spreadsheets/d/abc/edit#gid=11",
        failures=failures,
        date_str="2026-05-19",
    )
    msg = state["messages"][0]
    assert msg["Subject"] == "Opportunity-Finder partial — week of 2026-05-19"
    body = msg.get_content()
    assert "reddit / scrape: 401 Unauthorized" in body
    assert "play_store / scrape: HTTP 429 rate limit" in body


def test_smtp_login_uses_configured_user_and_password(captured):
    factory, state = captured
    notifier = make_notifier(factory)
    notifier.send_ready_email(
        sheet_url="https://x",
        stats={"date": "2026-05-19", "n_candidates": 0, "n_fast": 0, "n_greenfield": 0, "cost": "0.00"},
    )
    assert state["logins"] == [("harris.jason121@gmail.com", "appPasswordHere")]


def test_smtp_factory_called_with_host_and_port(captured):
    factory, state = captured
    notifier = make_notifier(factory)
    notifier.send_ready_email(
        sheet_url="https://x",
        stats={"date": "2026-05-19", "n_candidates": 0, "n_fast": 0, "n_greenfield": 0, "cost": "0.00"},
    )
    assert state["host_port"] == ("smtp.gmail.com", 587)


def test_ready_email_stats_field_substitution(captured):
    factory, state = captured
    notifier = make_notifier(factory)
    notifier.send_ready_email(
        sheet_url="https://example.com/sheet",
        stats={
            "date": "2026-07-10",
            "n_candidates": 156,
            "n_fast": 23,
            "n_greenfield": 133,
            "cost": "2.05",
        },
    )
    body = state["messages"][0].get_content()
    for needle in ("156", "23", "133", "2.05", "https://example.com/sheet", "2026-07-10"):
        assert needle in body or needle in state["messages"][0]["Subject"]
