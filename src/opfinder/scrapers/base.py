"""Scraper Protocol — see design doc §5.1."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from ..models import Candidate


class ScraperBase(Protocol):
    name: str

    def is_configured(self) -> bool:
        """Return True iff this scraper has the credentials/config it needs to run.

        Callable on a class as well as an instance — implementations are
        typically classmethods so main.py can check before constructing.
        """
        ...

    def fetch(self, since: datetime) -> list[Candidate]:
        """Return new candidates first seen since the given timestamp."""
        ...
