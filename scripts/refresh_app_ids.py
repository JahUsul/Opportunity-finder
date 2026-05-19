"""Refresh app_ids in config/sources.yaml from Apple and Google Play top charts.

Idempotent: overwrites the `app_ids` lists with the current top 50 per category
in productivity, business, and lifestyle (150 entries per store).

    uv run python scripts/refresh_app_ids.py
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import httpx
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCES_YAML = PROJECT_ROOT / "config" / "sources.yaml"

CATEGORIES = ("productivity", "business", "lifestyle")

# Apple Marketing Tools feed genre IDs.
APPLE_GENRES = {
    "productivity": 6007,
    "business": 6000,
    "lifestyle": 6012,
}

# Google Play category constants (per google_play_scraper.Category) are upper-case.
PLAY_CATEGORY_CONSTANTS = {
    "productivity": "PRODUCTIVITY",
    "business": "BUSINESS",
    "lifestyle": "LIFESTYLE",
}


def fetch_apple_top(category: str, *, country: str = "us", limit: int = 50) -> list[dict]:
    genre_id = APPLE_GENRES[category]
    url = (
        f"https://rss.applemarketingtools.com/api/v2/{country}/apps/top-free/"
        f"{limit}/apps.json?genre={genre_id}"
    )
    # Apple redirects this domain to rss.marketingtools.apple.com; httpx is opt-in unlike requests.
    resp = httpx.get(url, timeout=30.0, follow_redirects=True)
    resp.raise_for_status()
    data = resp.json()
    apps: list[dict] = []
    for item in data.get("feed", {}).get("results", []):
        apps.append(
            {
                "id": str(item["id"]),
                "name": item.get("name", ""),
                "slug": _slug_from_url(item.get("url", "")),
                "category": category,
            }
        )
    return apps


def _slug_from_url(url: str) -> str:
    if not url:
        return ""
    parts = url.rstrip("/").split("/")
    # e.g., https://apps.apple.com/us/app/microsoft-excel/id342792525
    if "app" in parts:
        idx = parts.index("app")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return ""


_PLAY_APP_ID_RE = re.compile(r"/store/apps/details\?id=([a-zA-Z][\w.]+)")


def fetch_play_top(
    category: str, *, country: str = "us", lang: str = "en", limit: int = 50
) -> list[dict]:
    """Pull top apps from a Play Store category page.

    `google-play-scraper` (the JoMingyu Python port) does NOT expose a `list()`
    function — only `app`, `reviews`, `search`. So we fetch the category page
    HTML directly to extract app IDs, then resolve each name via `app()`.
    """
    from google_play_scraper import app as gp_app

    cat = PLAY_CATEGORY_CONSTANTS[category]
    url = (
        f"https://play.google.com/store/apps/category/{cat}"
        f"?gl={country}&hl={lang}"
    )
    resp = httpx.get(url, timeout=30.0, follow_redirects=True)
    resp.raise_for_status()

    seen: list[str] = []
    seen_set: set[str] = set()
    for app_id in _PLAY_APP_ID_RE.findall(resp.text):
        if app_id in seen_set:
            continue
        seen_set.add(app_id)
        seen.append(app_id)
        if len(seen) >= limit:
            break

    apps: list[dict] = []
    for app_id in seen:
        try:
            details = gp_app(app_id, lang=lang, country=country)
        except Exception as e:
            print(f"    warn: app({app_id}) failed ({e}); skipping", file=sys.stderr)
            continue
        apps.append(
            {
                "id": app_id,
                "name": details.get("title") or "",
                "category": category,
            }
        )
        time.sleep(0.1)  # gentle rate limit
    return apps


def main() -> int:
    if not SOURCES_YAML.exists():
        print(f"sources.yaml not found at {SOURCES_YAML}", file=sys.stderr)
        return 1

    cfg = yaml.safe_load(SOURCES_YAML.read_text()) or {}

    apple_apps: list[dict] = []
    for cat in CATEGORIES:
        print(f"  apple: fetching top 50 / {cat}...", file=sys.stderr)
        apple_apps.extend(fetch_apple_top(cat))

    play_apps: list[dict] = []
    for cat in CATEGORIES:
        print(f"  play:  fetching top 50 / {cat}...", file=sys.stderr)
        play_apps.extend(fetch_play_top(cat))

    cfg.setdefault("app_store", {})["app_ids"] = apple_apps
    cfg.setdefault("play_store", {})["app_ids"] = play_apps

    SOURCES_YAML.write_text(
        yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True, default_flow_style=False)
    )
    print(
        f"wrote {len(apple_apps)} App Store + {len(play_apps)} Play Store apps to "
        f"{SOURCES_YAML.relative_to(PROJECT_ROOT)}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
