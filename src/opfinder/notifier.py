"""SMTP notifier — see design doc §5.6."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Any, Callable

log = logging.getLogger(__name__)


READY_TEMPLATE = """\
{n_candidates} candidates scored this week. Top 50 ranked in your sheet.
{n_fast} in the fast lane (OSS-leverageable), {n_greenfield} in greenfield.

Sheet: {sheet_url}

Cost this run: ${cost}.

— Opportunity-Finder
"""

PARTIAL_TEMPLATE_HEADER = """\
Pipeline ran with one or more stage failures. Partial output written.

Sheet: {sheet_url}

Failures:
"""


class Notifier:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        user: str,
        password: str,
        notify_to: str,
        smtp_factory: Callable[[str, int], Any] | None = None,
    ) -> None:
        self._host = host
        self._port = int(port)
        self._user = user
        self._password = password
        self._notify_to = notify_to
        self._smtp_factory = smtp_factory or smtplib.SMTP

    def send_ready_email(self, sheet_url: str, stats: dict) -> None:
        date_str = stats.get("date", "")
        body = READY_TEMPLATE.format(
            n_candidates=stats.get("n_candidates", 0),
            n_fast=stats.get("n_fast", 0),
            n_greenfield=stats.get("n_greenfield", 0),
            sheet_url=sheet_url,
            cost=stats.get("cost", "0.00"),
        )
        self._send(
            subject=f"Opportunity-Finder ready — week of {date_str}",
            body=body,
        )

    def send_partial_email(
        self, sheet_url: str, failures: list[dict], date_str: str = ""
    ) -> None:
        lines = [
            PARTIAL_TEMPLATE_HEADER.format(sheet_url=sheet_url),
        ]
        for f in failures:
            lines.append(
                f"  - {f.get('source', '?')} / {f.get('stage', '?')}: {f.get('error', '?')}"
            )
        lines.append("\n— Opportunity-Finder\n")
        self._send(
            subject=f"Opportunity-Finder partial — week of {date_str}",
            body="\n".join(lines),
        )

    def _send(self, *, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self._user
        msg["To"] = self._notify_to
        msg.set_content(body)

        with self._smtp_factory(self._host, self._port) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(self._user, self._password)
            smtp.send_message(msg)
        log.info("notifier: sent %r to %s", subject, self._notify_to)
