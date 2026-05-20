"""LLM scorer with Layer-2 injection pre-scan + Layer-5 reasoning discard.

See design doc §5.3. The LLM produces a `reasoning` field as part of every
scored response; that field is logged at DEBUG only and **never** assigned
to the Candidate, written to the sheet, or surfaced in any non-DEBUG log
stream. Only the integer score is persisted.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import anthropic

from . import injection_patterns
from .models import Candidate

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-haiku-4-5-20251001"

# Claude Haiku 4.5 published rates (USD per million tokens).
HAIKU_INPUT_PRICE_PER_M = 1.0
HAIKU_OUTPUT_PRICE_PER_M = 5.0

BUDGET_WARN_USD = 60.0
BUDGET_ABORT_USD = 100.0

_PROMPTS_DIR = Path(__file__).parent / "prompts"
PAIN_TEMPLATE = (_PROMPTS_DIR / "pain.txt").read_text()
MONEY_TEMPLATE = (_PROMPTS_DIR / "money.txt").read_text()
BUYER_B2B_TEMPLATE = (_PROMPTS_DIR / "buyer_b2b.txt").read_text()
BUYER_APPSTORE_TEMPLATE = (_PROMPTS_DIR / "buyer_appstore.txt").read_text()

_APPSTORE_SOURCES = {"app_store", "play_store"}


class BudgetExceededError(RuntimeError):
    """Raised when projected weekly cost exceeds BUDGET_ABORT_USD."""


class Scorer:
    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        *,
        client: Any | None = None,
        concurrency: int = 5,
    ) -> None:
        self._client = client if client is not None else anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._semaphore = asyncio.Semaphore(concurrency)
        self._tokens_in = 0
        self._tokens_out = 0
        self._warned = False

    async def score(self, candidate: Candidate) -> Candidate:
        self._prescan(candidate)
        buyer_template = (
            BUYER_APPSTORE_TEMPLATE if candidate.source in _APPSTORE_SOURCES else BUYER_B2B_TEMPLATE
        )
        pain, money, buyer = await asyncio.gather(
            self._score_signal("pain", PAIN_TEMPLATE, candidate),
            self._score_signal("money", MONEY_TEMPLATE, candidate),
            self._score_signal("buyer", buyer_template, candidate),
        )
        candidate.pain = pain
        candidate.money = money
        candidate.buyer = buyer
        return candidate

    def score_batch(self, candidates: list[Candidate]) -> list[Candidate]:
        async def _run() -> list[Candidate]:
            return list(await asyncio.gather(*(self.score(c) for c in candidates)))

        return asyncio.run(_run())

    def _prescan(self, candidate: Candidate) -> None:
        matches = injection_patterns.scan(candidate.body or "")
        if not matches:
            return
        candidate.injection_flag = True
        for m in matches:
            candidate.injection_patterns.append(m.name)
            log.warning(
                "injection pattern %r matched in candidate %s (category=%s, severity=%s)",
                m.name, candidate.id, m.category, m.severity,
            )

    async def _score_signal(
        self, signal: str, template: str, candidate: Candidate
    ) -> int:
        prompt = (
            template
            .replace("{source}", candidate.source)
            .replace("{body}", candidate.body or "")
        )
        async with self._semaphore:
            resp = await self._client.messages.create(
                model=self._model,
                max_tokens=200,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )
        self._track_usage(resp)
        self._enforce_budget()

        score, reasoning = _parse_score_response(resp)
        # Reasoning is logged at DEBUG only and never persisted to the Candidate.
        log.debug("Score %s for %s: reasoning=%s", signal, candidate.id, reasoning)
        return max(0, min(10, score))

    def _track_usage(self, resp: Any) -> None:
        usage = getattr(resp, "usage", None)
        if usage is None:
            return
        self._tokens_in += int(getattr(usage, "input_tokens", 0) or 0)
        self._tokens_out += int(getattr(usage, "output_tokens", 0) or 0)

    def _projected_cost(self) -> float:
        return (
            self._tokens_in * HAIKU_INPUT_PRICE_PER_M / 1_000_000
            + self._tokens_out * HAIKU_OUTPUT_PRICE_PER_M / 1_000_000
        )

    def _enforce_budget(self) -> None:
        cost = self._projected_cost()
        if cost > BUDGET_ABORT_USD:
            raise BudgetExceededError(
                f"projected cost ${cost:.2f} exceeds abort threshold ${BUDGET_ABORT_USD:.2f}"
            )
        if cost > BUDGET_WARN_USD and not self._warned:
            log.warning(
                "projected cost $%.2f exceeds warn threshold $%.2f", cost, BUDGET_WARN_USD,
            )
            self._warned = True


def _parse_score_response(resp: Any) -> tuple[int, str]:
    """Pull the JSON {"score": ..., "reasoning": ...} payload out of a Message response."""
    text = ""
    for block in getattr(resp, "content", []) or []:
        block_text = getattr(block, "text", None)
        if block_text:
            text = block_text
            break
    if not text:
        return 0, ""
    try:
        parsed = json.loads(_strip_fences(text))
    except json.JSONDecodeError:
        log.warning("scorer could not parse JSON from response: %r", text[:200])
        return 0, ""
    try:
        score = int(parsed.get("score", 0))
    except (TypeError, ValueError):
        score = 0
    reasoning = str(parsed.get("reasoning", ""))
    return score, reasoning


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        # Drop opening fence (with optional language) and closing fence.
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[: -3]
    return text.strip()
