from datetime import date, datetime
from unittest.mock import MagicMock

import pytest

from opfinder.models import Candidate
from opfinder.sheet_writer import (
    COL_INDEX_TOTAL,
    HIDDEN_COLUMNS,
    PRIMARY_TAB_LIMIT,
    RAW_EXCERPT_COL,
    SCHEMA,
    SCORE_COLUMNS,
    URL_COLUMNS,
    SheetWriter,
)


def _layout_requests(sheet_mock):
    """Pull the requests list passed to the single batch_update call."""
    sheet_mock.batch_update.assert_called_once()
    return sheet_mock.batch_update.call_args[0][0]["requests"]


def _find_request(requests, key):
    return next(r for r in requests if key in r)


def make_candidate(
    *, id_: str = "c1", source: str = "hn", title: str = "t", machine_total: int = 10,
    pain: int = 3, money: int = 3, buyer: int = 3, oss: int = 1,
    source_url: str = "https://example.com/a",
    github_repo_url: str | None = None,
    injection_flag: bool = False,
    lane: str = "greenfield",
):
    return Candidate(
        id=id_,
        source=source,
        source_url=source_url,
        author_id="alice",
        title=title,
        body="b",
        raw_excerpt="excerpt",
        scraped_at=datetime(2026, 5, 19),
        pain=pain,
        money=money,
        buyer=buyer,
        oss=oss,
        github_repo_url=github_repo_url,
        lane=lane,
        machine_total=machine_total,
        injection_flag=injection_flag,
    )


@pytest.fixture
def mock_gspread():
    primary_ws = MagicMock()
    primary_ws.id = 11
    overflow_ws = MagicMock()
    overflow_ws.id = 22

    spreadsheet = MagicMock()
    spreadsheet.add_worksheet.side_effect = [primary_ws, overflow_ws]

    gc = MagicMock()
    gc.open_by_key.return_value = spreadsheet

    return gc, spreadsheet, primary_ws, overflow_ws


def build_writer(mock_gspread, *, promotion_threshold: int = 50) -> SheetWriter:
    gc, *_ = mock_gspread
    return SheetWriter(
        oauth_client_path="dummy.json",
        oauth_token_path="token.json",
        spreadsheet_id="SHEET_ID_ABC",
        promotion_threshold=promotion_threshold,
        client_factory=lambda: gc,
    )


def test_tab_named_iso_week_date(mock_gspread):
    gc, sheet, primary_ws, _ = mock_gspread
    writer = build_writer(mock_gspread)
    writer.write_week([make_candidate()], date(2026, 5, 19))
    first_call = sheet.add_worksheet.call_args_list[0]
    assert first_call.kwargs["title"] == "2026-05-19"


def test_open_by_key_uses_configured_spreadsheet_id(mock_gspread):
    gc, *_ = mock_gspread
    writer = build_writer(mock_gspread)
    writer.write_week([], date(2026, 5, 19))
    gc.open_by_key.assert_called_once_with("SHEET_ID_ABC")


def test_column_headers_exactly_match_design_doc_schema(mock_gspread):
    _, _, primary_ws, _ = mock_gspread
    writer = build_writer(mock_gspread)
    writer.write_week([make_candidate()], date(2026, 5, 19))
    call = primary_ws.update.call_args
    values = call.kwargs.get("values", call.args[0] if call.args else None)
    headers = values[0]
    assert headers == SCHEMA
    assert len(headers) == 24  # A through X
    assert headers[-1] == "injection_flag"  # column X


def test_rows_written_in_machine_total_descending_order(mock_gspread):
    _, _, primary_ws, _ = mock_gspread
    candidates = [
        make_candidate(id_="low", machine_total=5),
        make_candidate(id_="high", machine_total=30),
        make_candidate(id_="mid", machine_total=15),
    ]
    writer = build_writer(mock_gspread)
    writer.write_week(candidates, date(2026, 5, 19))
    call = primary_ws.update.call_args
    values = call.kwargs.get("values", call.args[0] if call.args else None)
    ids_in_order = [row[0] for row in values[1:]]
    assert ids_in_order == ["high", "mid", "low"]


def test_primary_capped_at_50_overflow_gets_rest(mock_gspread):
    _, sheet, primary_ws, overflow_ws = mock_gspread
    candidates = [make_candidate(id_=f"c{i}", machine_total=i) for i in range(75)]
    writer = build_writer(mock_gspread)
    writer.write_week(candidates, date(2026, 5, 19))

    # Two tabs created.
    assert sheet.add_worksheet.call_count == 2
    titles = [c.kwargs["title"] for c in sheet.add_worksheet.call_args_list]
    assert titles == ["2026-05-19", "2026-05-19-overflow"]

    # Primary has 50 data rows + 1 header.
    primary_values = primary_ws.update.call_args.kwargs.get("values")
    assert len(primary_values) == PRIMARY_TAB_LIMIT + 1

    # Overflow has 25 data rows + 1 header.
    overflow_values = overflow_ws.update.call_args.kwargs.get("values")
    assert len(overflow_values) == (75 - PRIMARY_TAB_LIMIT) + 1


def test_no_overflow_tab_when_under_50_candidates(mock_gspread):
    _, sheet, _, _ = mock_gspread
    candidates = [make_candidate(id_=f"c{i}", machine_total=i) for i in range(10)]
    writer = build_writer(mock_gspread)
    writer.write_week(candidates, date(2026, 5, 19))
    assert sheet.add_worksheet.call_count == 1


def test_source_url_column_uses_hyperlink_formula(mock_gspread):
    _, _, primary_ws, _ = mock_gspread
    c = make_candidate(source_url="https://news.ycombinator.com/item?id=42")
    build_writer(mock_gspread).write_week([c], date(2026, 5, 19))
    values = primary_ws.update.call_args.kwargs.get("values")
    row = values[1]
    source_url_idx = SCHEMA.index("source_url")
    assert row[source_url_idx].startswith("=HYPERLINK(")
    assert "https://news.ycombinator.com/item?id=42" in row[source_url_idx]


def test_github_repo_column_hyperlinked_when_set(mock_gspread):
    _, _, primary_ws, _ = mock_gspread
    c = make_candidate(github_repo_url="https://github.com/owner/repo")
    build_writer(mock_gspread).write_week([c], date(2026, 5, 19))
    values = primary_ws.update.call_args.kwargs.get("values")
    row = values[1]
    github_idx = SCHEMA.index("github_repo")
    assert row[github_idx].startswith("=HYPERLINK(")
    assert "github.com/owner/repo" in row[github_idx]


def test_github_repo_column_empty_when_not_set(mock_gspread):
    _, _, primary_ws, _ = mock_gspread
    c = make_candidate(github_repo_url=None)
    build_writer(mock_gspread).write_week([c], date(2026, 5, 19))
    values = primary_ws.update.call_args.kwargs.get("values")
    row = values[1]
    assert row[SCHEMA.index("github_repo")] == ""


def test_total_formula_uses_correct_row_number(mock_gspread):
    _, _, primary_ws, _ = mock_gspread
    candidates = [
        make_candidate(id_="a", machine_total=5),
        make_candidate(id_="b", machine_total=3),
    ]
    build_writer(mock_gspread).write_week(candidates, date(2026, 5, 19))
    values = primary_ws.update.call_args.kwargs.get("values")
    # Row 2 (first data row) total formula should reference row 2.
    total_idx = SCHEMA.index("total")
    assert values[1][total_idx] == "=M2+S2"
    assert values[2][total_idx] == "=M3+S3"


def test_machine_total_formula_present(mock_gspread):
    _, _, primary_ws, _ = mock_gspread
    build_writer(mock_gspread).write_week([make_candidate()], date(2026, 5, 19))
    values = primary_ws.update.call_args.kwargs.get("values")
    mt_idx = SCHEMA.index("machine_total")
    assert values[1][mt_idx] == "=H2+I2+J2+K2"


def test_conditional_format_applied_to_total_column(mock_gspread):
    _, sheet, _, _ = mock_gspread
    build_writer(mock_gspread, promotion_threshold=42).write_week(
        [make_candidate()], date(2026, 5, 19)
    )
    requests = _layout_requests(sheet)
    rule = _find_request(requests, "addConditionalFormatRule")[
        "addConditionalFormatRule"
    ]["rule"]
    rng = rule["ranges"][0]
    assert rng["startColumnIndex"] == COL_INDEX_TOTAL
    assert rng["endColumnIndex"] == COL_INDEX_TOTAL + 1
    cond = rule["booleanRule"]["condition"]
    assert cond["type"] == "NUMBER_GREATER_THAN_EQ"
    assert cond["values"][0]["userEnteredValue"] == "42"


def test_no_formatting_when_no_data_rows(mock_gspread):
    _, sheet, _, _ = mock_gspread
    build_writer(mock_gspread).write_week([], date(2026, 5, 19))
    sheet.batch_update.assert_not_called()


def test_layout_hides_system_columns(mock_gspread):
    _, sheet, _, _ = mock_gspread
    build_writer(mock_gspread).write_week([make_candidate()], date(2026, 5, 19))
    requests = _layout_requests(sheet)
    hidden_idxs = {
        r["updateDimensionProperties"]["range"]["startIndex"]
        for r in requests
        if "updateDimensionProperties" in r
        and r["updateDimensionProperties"]["properties"].get("hiddenByUser") is True
    }
    assert hidden_idxs == set(HIDDEN_COLUMNS)


def test_layout_freezes_header_row(mock_gspread):
    _, sheet, _, _ = mock_gspread
    build_writer(mock_gspread).write_week([make_candidate()], date(2026, 5, 19))
    requests = _layout_requests(sheet)
    freeze = _find_request(requests, "updateSheetProperties")["updateSheetProperties"]
    assert freeze["properties"]["gridProperties"]["frozenRowCount"] == 1


def test_layout_wraps_raw_excerpt_column(mock_gspread):
    _, sheet, _, _ = mock_gspread
    build_writer(mock_gspread).write_week([make_candidate()], date(2026, 5, 19))
    requests = _layout_requests(sheet)
    wrap = _find_request(requests, "repeatCell")["repeatCell"]
    assert wrap["range"]["startColumnIndex"] == RAW_EXCERPT_COL
    assert wrap["range"]["endColumnIndex"] == RAW_EXCERPT_COL + 1
    assert wrap["cell"]["userEnteredFormat"]["wrapStrategy"] == "WRAP"


def test_layout_sets_column_widths(mock_gspread):
    _, sheet, _, _ = mock_gspread
    build_writer(mock_gspread).write_week([make_candidate()], date(2026, 5, 19))
    requests = _layout_requests(sheet)
    widths: dict[int, int] = {}
    for r in requests:
        if "updateDimensionProperties" not in r:
            continue
        props = r["updateDimensionProperties"]["properties"]
        if "pixelSize" not in props:
            continue
        idx = r["updateDimensionProperties"]["range"]["startIndex"]
        widths[idx] = props["pixelSize"]
    assert widths[RAW_EXCERPT_COL] == 400
    for idx in URL_COLUMNS:
        assert widths[idx] == 200
    for idx in SCORE_COLUMNS:
        assert widths[idx] == 70


def test_returns_url_with_primary_tab_gid(mock_gspread):
    writer = build_writer(mock_gspread)
    url = writer.write_week([make_candidate()], date(2026, 5, 19))
    assert url == "https://docs.google.com/spreadsheets/d/SHEET_ID_ABC/edit#gid=11"


def test_injection_flag_column_carries_value(mock_gspread):
    _, _, primary_ws, _ = mock_gspread
    c = make_candidate(injection_flag=True)
    build_writer(mock_gspread).write_week([c], date(2026, 5, 19))
    values = primary_ws.update.call_args.kwargs.get("values")
    flag_idx = SCHEMA.index("injection_flag")
    assert values[1][flag_idx] is True
