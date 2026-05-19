"""Scraper Protocol — see design doc §5.1."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from ..models import Candidate


class ScraperBase(Protocol):
    name: str

    def fetch(self, since: datetime) -> list[Candidate]:
        """Return new candidates first seen since the given timestamp."""
        ...
