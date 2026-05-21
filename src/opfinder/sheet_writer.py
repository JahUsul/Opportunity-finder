"""Google Sheets writer — see design doc §5.5 (OAuth-pivoted 2026-05-19).

Writes the §4.3 column schema (A–X). Top 50 by machine_total on the primary
tab, the rest on a sibling overflow tab. Hyperlink columns E/L, conditional
formatting on column T (total) for rows clearing the promotion threshold.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Callable

import gspread

from .models import Candidate

log = logging.getLogger(__name__)

# §4.3 schema. Order is load-bearing — column letters are derived by index.
SCHEMA: list[str] = [
    "id",             # A
    "week_run",       # B
    "first_seen",     # C
    "source",         # D
    "source_url",     # E (hyperlinked)
    "author_id",      # F
    "raw_excerpt",    # G
    "pain",           # H
    "money",          # I
    "buyer",          # J
    "oss",            # K
    "github_repo",    # L (hyperlinked)
    "machine_total",  # M  formula =H+I+J+K
    "lane",           # N
    "triage",         # O  (human)
    "fit",            # P  (human)
    "reach",          # Q  (human)
    "validation",     # R  (human)
    "human_total",    # S  formula =P+Q+R
    "total",          # T  formula =M+S  (conditional-format target)
    "decision",       # U  (human)
    "notes",          # V  (human)
    "ignore_forever", # W  (human → writes back to dedup)
    "injection_flag", # X  (Layer-2 pre-scan result)
]

# 0-indexed columns of interest.
COL_INDEX_TOTAL = SCHEMA.index("total")          # T → 19

# Layout: which columns the human triage view hides, which get wider/narrower.
HIDDEN_COLUMNS = (
    SCHEMA.index("id"),          # A
    SCHEMA.index("week_run"),    # B
    SCHEMA.index("first_seen"),  # C
    SCHEMA.index("author_id"),   # F
)
RAW_EXCERPT_COL = SCHEMA.index("raw_excerpt")    # G
URL_COLUMNS = (
    SCHEMA.index("source_url"),  # E
    SCHEMA.index("github_repo"), # L
)
SCORE_COLUMNS = (
    SCHEMA.index("pain"),          # H
    SCHEMA.index("money"),         # I
    SCHEMA.index("buyer"),         # J
    SCHEMA.index("oss"),           # K
    SCHEMA.index("machine_total"), # M
    SCHEMA.index("fit"),           # P
    SCHEMA.index("reach"),         # Q
    SCHEMA.index("validation"),    # R
    SCHEMA.index("human_total"),   # S
    SCHEMA.index("total"),         # T
)

PRIMARY_TAB_LIMIT = 50

DEFAULT_PROMOTION_THRESHOLD = 50


class SheetWriter:
    def __init__(
        self,
        oauth_client_path: str,
        oauth_token_path: str,
        spreadsheet_id: str,
        *,
        promotion_threshold: int = DEFAULT_PROMOTION_THRESHOLD,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._oauth_client_path = oauth_client_path
        self._oauth_token_path = oauth_token_path
        self._spreadsheet_id = spreadsheet_id
        self._promotion_threshold = int(promotion_threshold)
        self._client_factory = client_factory
        self._gc: Any | None = None
        self._spreadsheet: Any | None = None

    # ----- public API -----

    def write_week(self, candidates: list[Candidate], week_date: date) -> str:
        self._connect()
        sorted_candidates = sorted(
            candidates,
            key=lambda c: (c.machine_total if c.machine_total is not None else -1),
            reverse=True,
        )
        primary = sorted_candidates[:PRIMARY_TAB_LIMIT]
        overflow = sorted_candidates[PRIMARY_TAB_LIMIT:]

        primary_name = week_date.isoformat()
        primary_ws = self._create_tab(primary_name, max(2, len(primary) + 1))
        self._write_rows(primary_ws, primary, week_date)
        self._apply_formatting(primary_ws, len(primary))

        if overflow:
            overflow_name = f"{primary_name}-overflow"
            overflow_ws = self._create_tab(overflow_name, len(overflow) + 1)
            self._write_rows(overflow_ws, overflow, week_date)
            self._apply_formatting(overflow_ws, len(overflow))

        return f"https://docs.google.com/spreadsheets/d/{self._spreadsheet_id}/edit#gid={primary_ws.id}"

    # ----- internals -----

    def _connect(self) -> None:
        if self._spreadsheet is not None:
            return
        if self._client_factory is not None:
            self._gc = self._client_factory()
        else:
            self._gc = gspread.oauth(
                credentials_filename=self._oauth_client_path,
                authorized_user_filename=self._oauth_token_path,
            )
        self._spreadsheet = self._gc.open_by_key(self._spreadsheet_id)

    def _create_tab(self, name: str, rows: int) -> Any:
        return self._spreadsheet.add_worksheet(title=name, rows=rows, cols=len(SCHEMA))

    def _write_rows(
        self, worksheet: Any, candidates: list[Candidate], week_date: date
    ) -> None:
        values: list[list[Any]] = [list(SCHEMA)]
        for idx, c in enumerate(candidates, start=2):  # data rows begin at row 2
            values.append(self._candidate_to_row(c, week_date, idx))
        worksheet.update(values=values, range_name="A1", value_input_option="USER_ENTERED")

    def _candidate_to_row(
        self, c: Candidate, week_date: date, row_num: int
    ) -> list[Any]:
        source_url_cell = (
            f'=HYPERLINK("{_escape(c.source_url)}", "{_escape(c.source_url)}")'
            if c.source_url else ""
        )
        github_repo_cell = (
            f'=HYPERLINK("{_escape(c.github_repo_url)}", "{_escape(c.github_repo_url)}")'
            if c.github_repo_url else ""
        )
        return [
            c.id,
            week_date.isoformat(),
            "",                                                # first_seen — TODO: surface from dedup
            c.source,
            source_url_cell,
            c.author_id,
            c.raw_excerpt or "",
            c.pain if c.pain is not None else "",
            c.money if c.money is not None else "",
            c.buyer if c.buyer is not None else "",
            c.oss if c.oss is not None else "",
            github_repo_cell,
            f"=H{row_num}+I{row_num}+J{row_num}+K{row_num}",   # machine_total
            c.lane or "",
            "",                                                # triage (human)
            "",                                                # fit (human)
            "",                                                # reach (human)
            "",                                                # validation (human)
            f"=P{row_num}+Q{row_num}+R{row_num}",              # human_total
            f"=M{row_num}+S{row_num}",                         # total
            "",                                                # decision (human)
            "",                                                # notes (human)
            False,                                             # ignore_forever (human)
            bool(c.injection_flag),                            # injection_flag
        ]

    def _apply_formatting(self, worksheet: Any, n_data_rows: int) -> None:
        """One batch_update covering conditional-format + readable layout.

        Layout is what makes the sheet usable on first open:
        - hide internal/system columns from default view
        - wrap raw_excerpt so long bodies don't overflow
        - widen raw_excerpt / URL columns, narrow score columns
        - freeze header row
        """
        if n_data_rows <= 0:
            return
        sheet_id = worksheet.id
        requests: list[dict] = [
            self._conditional_format_request(sheet_id, n_data_rows),
            self._freeze_header_request(sheet_id),
        ]
        requests.extend(self._hide_column_requests(sheet_id))
        requests.extend(self._column_width_requests(sheet_id))
        requests.append(self._wrap_excerpt_request(sheet_id))
        self._spreadsheet.batch_update({"requests": requests})

    def _conditional_format_request(self, sheet_id: int, n_data_rows: int) -> dict:
        return {
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [
                        {
                            "sheetId": sheet_id,
                            "startRowIndex": 1,                        # skip header
                            "endRowIndex": n_data_rows + 1,
                            "startColumnIndex": COL_INDEX_TOTAL,
                            "endColumnIndex": COL_INDEX_TOTAL + 1,
                        }
                    ],
                    "booleanRule": {
                        "condition": {
                            "type": "NUMBER_GREATER_THAN_EQ",
                            "values": [
                                {"userEnteredValue": str(self._promotion_threshold)}
                            ],
                        },
                        "format": {
                            "backgroundColor": {
                                "red": 0.72, "green": 0.88, "blue": 0.72,
                            },
                        },
                    },
                },
                "index": 0,
            }
        }

    def _freeze_header_request(self, sheet_id: int) -> dict:
        return {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {"frozenRowCount": 1},
                },
                "fields": "gridProperties.frozenRowCount",
            }
        }

    def _hide_column_requests(self, sheet_id: int) -> list[dict]:
        return [
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": idx,
                        "endIndex": idx + 1,
                    },
                    "properties": {"hiddenByUser": True},
                    "fields": "hiddenByUser",
                }
            }
            for idx in HIDDEN_COLUMNS
        ]

    def _column_width_requests(self, sheet_id: int) -> list[dict]:
        widths: list[tuple[int, int]] = [
            (RAW_EXCERPT_COL, 400),
        ]
        widths.extend((idx, 200) for idx in URL_COLUMNS)
        widths.extend((idx, 70) for idx in SCORE_COLUMNS)
        return [
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": idx,
                        "endIndex": idx + 1,
                    },
                    "properties": {"pixelSize": px},
                    "fields": "pixelSize",
                }
            }
            for idx, px in widths
        ]

    def _wrap_excerpt_request(self, sheet_id: int) -> dict:
        return {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startColumnIndex": RAW_EXCERPT_COL,
                    "endColumnIndex": RAW_EXCERPT_COL + 1,
                },
                "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
                "fields": "userEnteredFormat.wrapStrategy",
            }
        }


def _escape(s: str) -> str:
    """Escape a string for inclusion in a HYPERLINK formula."""
    return (s or "").replace('"', '""')
