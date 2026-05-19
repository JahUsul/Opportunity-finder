"""SQLite-backed dedup for scraped candidates.

Schema and rules: see opportunity-finder-design-doc.md §4.2 and §5.2.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from .models import Candidate

DEDUP_WINDOW_DAYS = 28

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_candidates (
    hash             TEXT PRIMARY KEY,
    source           TEXT NOT NULL,
    source_url       TEXT NOT NULL,
    first_seen_week  TEXT NOT NULL,
    last_seen_week   TEXT NOT NULL,
    status           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_seen_status ON seen_candidates(status);
CREATE INDEX IF NOT EXISTS idx_seen_last   ON seen_candidates(last_seen_week);
"""


def _normalize(text: str) -> str:
    return _NON_ALNUM.sub("", text.lower())


def compute_hash(source: str, title: str, author_id: str) -> str:
    payload = f"{source}|{_normalize(title)}|{author_id}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


class DedupStore:
    def __init__(self, db_path: Path | str, *, today: date | None = None) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._today = today
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def _now(self) -> date:
        return self._today if self._today is not None else date.today()

    def filter_new(self, candidates: list[Candidate]) -> list[Candidate]:
        """Apply dedup rules from §4.2, mutate SQLite, return survivors to score."""
        today = self._now()
        today_iso = today.isoformat()
        cutoff = today - timedelta(days=DEDUP_WINDOW_DAYS)
        survivors: list[Candidate] = []

        for c in candidates:
            if not c.dedup_hash:
                c.dedup_hash = compute_hash(c.source, c.title, c.author_id)

            row = self._conn.execute(
                "SELECT status, last_seen_week FROM seen_candidates WHERE hash = ?",
                (c.dedup_hash,),
            ).fetchone()

            if row is None:
                self._conn.execute(
                    "INSERT INTO seen_candidates "
                    "(hash, source, source_url, first_seen_week, last_seen_week, status) "
                    "VALUES (?, ?, ?, ?, ?, 'active')",
                    (c.dedup_hash, c.source, c.source_url, today_iso, today_iso),
                )
                survivors.append(c)
                continue

            status, last_seen = row
            if status == "ignored_forever":
                continue

            self._conn.execute(
                "UPDATE seen_candidates SET last_seen_week = ? WHERE hash = ?",
                (today_iso, c.dedup_hash),
            )
            if date.fromisoformat(last_seen) < cutoff:
                survivors.append(c)

        self._conn.commit()
        return survivors

    def mark_ignored(self, hashes: list[str]) -> None:
        if not hashes:
            return
        self._conn.executemany(
            "UPDATE seen_candidates SET status = 'ignored_forever' WHERE hash = ?",
            [(h,) for h in hashes],
        )
        self._conn.commit()

    def stats(self) -> dict:
        cur = self._conn.cursor()
        total = cur.execute("SELECT COUNT(*) FROM seen_candidates").fetchone()[0]
        by_status = {
            row[0]: row[1]
            for row in cur.execute(
                "SELECT status, COUNT(*) FROM seen_candidates GROUP BY status"
            )
        }
        by_source = {
            row[0]: row[1]
            for row in cur.execute(
                "SELECT source, COUNT(*) FROM seen_candidates GROUP BY source"
            )
        }
        cutoff_iso = (self._now() - timedelta(days=DEDUP_WINDOW_DAYS)).isoformat()
        recent = cur.execute(
            "SELECT COUNT(*) FROM seen_candidates WHERE last_seen_week >= ?",
            (cutoff_iso,),
        ).fetchone()[0]
        return {
            "total": total,
            "by_status": by_status,
            "by_source": by_source,
            "recent": recent,
            "old": total - recent,
        }

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "DedupStore":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
