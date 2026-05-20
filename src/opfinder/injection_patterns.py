"""Layer-2 prompt-injection patterns — see design doc §5.3.

A versioned list of regex patterns scanned against `Candidate.body` *before*
the LLM call. Matches set `candidate.injection_flag = True` and append the
pattern name to `candidate.injection_patterns`; they do not block scoring.

Pattern categories:
- instruction_override: phrases that try to wipe or override prior context
- role_impersonation:   phrases that try to redefine model identity / role
- exfiltration:         phrases that try to leak data outbound
- encoding_evasion:     long base64 / hex / url-encoded blobs and invisible chars
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Pattern:
    name: str
    regex: re.Pattern
    category: str
    severity: str


def _build(category: str, specs: list[tuple[str, str, str]]) -> list[Pattern]:
    return [
        Pattern(
            name=name,
            regex=re.compile(pattern, re.IGNORECASE),
            category=category,
            severity=severity,
        )
        for name, pattern, severity in specs
    ]


INSTRUCTION_OVERRIDE: list[Pattern] = _build(
    "instruction_override",
    [
        ("ignore_previous_instructions",
         r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?", "high"),
        ("disregard_previous_instructions",
         r"disregard\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?)", "high"),
        ("forget_everything",
         r"forget\s+(everything|all|your\s+previous|the\s+above)", "high"),
        ("system_override",
         r"system\s+override", "high"),
        ("new_instructions_marker",
         r"new\s+instructions?:\s*", "medium"),
    ],
)

ROLE_IMPERSONATION: list[Pattern] = _build(
    "role_impersonation",
    [
        ("as_an_ai_assistant",
         r"as\s+an?\s+AI\s+(assistant|language\s+model|model)", "high"),
        ("you_are_now",
         r"you\s+are\s+now\s+(a|an|the)\s+\w+", "high"),
        ("user_actually_wants",
         r"the\s+user\s+(actually|really)\s+wants?", "medium"),
        ("i_am_system",
         r"i\s+am\s+(the\s+)?(system|developer|admin|operator|anthropic)", "high"),
        ("act_as",
         r"act\s+as\s+(if\s+you\s+were\s+|a\s+|an\s+)", "medium"),
    ],
)

EXFILTRATION: list[Pattern] = _build(
    "exfiltration",
    [
        ("send_contact_list",
         r"send\s+(all\s+)?(the\s+)?(contact|email|user|client)\s+(list|data|info)", "high"),
        ("email_all_clients",
         r"email\s+(all|every|the)\s+(clients?|users?|contacts?|customers?)", "high"),
        ("post_data_to",
         r"post\s+(all|the)\s+(notes?|messages?|data|content)\s+to", "high"),
        ("share_secrets",
         r"share\s+(your|the|all)\s+(secrets?|passwords?|tokens?|keys?|credentials?)", "high"),
        ("reveal_system_prompt",
         r"reveal\s+(your|the|all)\s+(system\s+prompt|instructions?|secrets?)", "high"),
    ],
)

ENCODING_EVASION: list[Pattern] = _build(
    "encoding_evasion",
    [
        ("long_base64_string",
         r"[A-Za-z0-9+/]{200,}={0,2}", "medium"),
        ("zero_width_chars",
         r"[​‌‍⁠﻿]", "high"),
        ("rtl_override",
         r"‮", "high"),
        ("long_hex_string",
         r"(?:0x|\\x)[0-9a-fA-F]{40,}", "medium"),
        ("long_url_encoded",
         r"%[0-9a-fA-F]{2}(?:%[0-9a-fA-F]{2}){20,}", "medium"),
    ],
)


ALL_PATTERNS: list[Pattern] = (
    INSTRUCTION_OVERRIDE
    + ROLE_IMPERSONATION
    + EXFILTRATION
    + ENCODING_EVASION
)


def scan(text: str) -> list[Pattern]:
    """Return every pattern whose regex finds a match in `text`."""
    if not text:
        return []
    return [p for p in ALL_PATTERNS if p.regex.search(text)]
