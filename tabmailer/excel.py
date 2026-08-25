"""Turn Tableau's bare crosstab exports into a formatted report workbook.

Tableau's ``/crosstab/excel`` endpoint returns a completely unstyled sheet: a
header row, raw values, no fills, no widths, no title.  This module reads that,
works out the shape of the crosstab (how many header rows, how many row-label
columns), and rewrites it in the house style defined in :mod:`tabmailer.style`.
"""

from __future__ import annotations

import datetime as dt
import io
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from . import style as S

# Rows whose first label looks like one of these get total styling.
_GRAND_TOTAL_RE = re.compile(r"^\s*(grand\s+total|overall\s+total|total)\s*$", re.I)
_SUBTOTAL_RE = re.compile(r"\btotal\b", re.I)
_INVALID_TAB_CHARS = re.compile(r"[\[\]:*?/\\]")


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


# ---------------------------------------------------------------------------
# Reading Tableau's export
# ---------------------------------------------------------------------------


@dataclass
class RawSheet:
    """One tab as Tableau exported it."""

    name: str
    rows: list[list[Any]]
    col_formats: list[str] = field(default_factory=list)

    @property
    def n_cols(self) -> int:
        return max((len(r) for r in self.rows), default=0)

    @property
    def n_rows(self) -> int:
        return len(self.rows)


def read_crosstab(data: bytes) -> list[RawSheet]:
    """Parse the bytes returned by Tableau's crosstab/excel endpoint.

    A crosstab download of a dashboard contains one tab per worksheet in that
    dashboard, so this always returns a list.
    """
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    sheets: list[RawSheet] = []
    for ws in wb.worksheets:
        rows: list[list[Any]] = []
        for row in ws.iter_rows(values_only=True):
            rows.append(list(row))
        # Trim trailing rows/columns that are entirely blank.
        while rows and all(_is_blank(v) for v in rows[-1]):
            rows.pop()
        n_cols = max((len(r) for r in rows), default=0)
        while n_cols > 0 and all(_is_blank(r[n_cols - 1]) if len(r) >= n_cols else True for r in rows):
            n_cols -= 1
        rows = [(r + [None] * n_cols)[:n_cols] for r in rows]

        # Sample Tableau's own number format per column - it carries the
        # percent/currency intent we want to preserve.
        col_formats: list[str] = []
        for c in range(1, n_cols + 1):
            fmt = "General"
            for r in range(2, min(ws.max_row, 40) + 1):
                cell = ws.cell(row=r, column=c)
                if cell.value is not None and cell.number_format != "General":
                    fmt = cell.number_format
                    break
            col_formats.append(fmt)

        if rows:
            sheets.append(RawSheet(name=ws.title, rows=rows, col_formats=col_formats))
    wb.close()
    return sheets


# ---------------------------------------------------------------------------
# Layout detection
# ---------------------------------------------------------------------------


@dataclass
class Layout:
    header_rows: int
    label_cols: int


def detect_layout(rows: Sequence[Sequence[Any]]) -> Layout:
    """Work out how many leading rows are headers and columns are row labels.

    Header rows are the block above the first genuinely numeric row; label
    columns are the leading columns that stay non-numeric in the data region.
    """
    if not rows:
        return Layout(1, 1)

    numeric_per_row = [sum(1 for v in r if _is_number(v)) for r in rows]
    peak = max(numeric_per_row, default=0)

    if peak == 0:
        header_rows = 1  # all-text crosstab
    else:
        threshold = max(1, peak * 0.5)
        header_rows = next(
            (i for i, n in enumerate(numeric_per_row) if n >= threshold), 1
        )
        header_rows = max(1, header_rows)
    header_rows = min(header_rows, max(1, len(rows) - 1))

    data = rows[header_rows:]
    n_cols = max((len(r) for r in rows), default=1)
    label_cols = 0
    for c in range(n_cols):
        values = [r[c] for r in data if c < len(r) and not _is_blank(r[c])]
        if not values:
            # A fully blank column inside the label block still belongs to it.
            if label_cols == c:
                label_cols = c + 1
                continue
            break
        numeric_share = sum(1 for v in values if _is_number(v)) / len(values)
        if numeric_share < 0.2:
            label_cols = c + 1
        else:
            break
    label_cols = max(1, min(label_cols, n_cols - 1 if n_cols > 1 else 1))
    return Layout(header_rows=header_rows, label_cols=label_cols)


# ---------------------------------------------------------------------------
# Number-format inference
# ---------------------------------------------------------------------------


def _infer_format(values: Iterable[Any], source_format: str, kit: S.StyleKit) -> str | None:
    vals = [v for v in values if not _is_blank(v)]
    if not vals:
        return None
    if any(isinstance(v, (dt.datetime, dt.date)) for v in vals):
        return S.DATETIME_FMT if any(
            isinstance(v, dt.datetime) and (v.hour or v.minute) for v in vals
        ) else S.DATE_FMT

    numeric = [v for v in vals if _is_number(v)]
    if not numeric:
        return None

    src = source_format or "General"
    if "%" in src:
        # Tableau already told us this is a percentage - keep its precision.
        return src if src.count("0") > 1 else S.PCT_2
    if "₹" in src or "$" in src or "€" in src or "£" in src:
        decimals = ".00" in src or any(abs(v - round(v)) > 1e-9 for v in numeric)
        return S.INR_2 if decimals else S.INR_0

    decimals = any(abs(float(v) - round(float(v))) > 1e-9 for v in numeric)
    return kit.acct(decimals)


def _display_len(value: Any, number_format: str | None) -> int:
    if _is_blank(value):
        return 0
    if isinstance(value, (dt.datetime, dt.date)):
        return 12
    if _is_number(value):
        if number_format and "%" in number_format:
            return len(f"{value * 100:,.2f}") + 1
        return len(f"{value:,.2f}")
    return max((len(part) for part in str(value).split("\n")), default=0)


# ---------------------------------------------------------------------------
# Report options
# ---------------------------------------------------------------------------


@dataclass
class ReportOptions:
    theme: str = S.DEFAULT_THEME
    report_title: str = "Report"
    report_date: dt.date | None = None
    subtitle: str = ""
    negatives_red: bool = False
    banded_rows: bool = False
    autofilter: bool = True
    freeze_panes: bool = True
    highlight_totals: bool = True
    contents_sheet: bool = True
    landscape: bool = True
    fit_to_width: bool = True

    def kit(self) -> S.StyleKit:
        theme = S.THEMES.get(self.theme, S.THEMES[S.DEFAULT_THEME])
        return S.StyleKit(theme=theme, negatives_red=self.negatives_red, banded=self.banded_rows)


@dataclass
class SheetSpec:
    """One tab to write into the report."""

    tab_name: str
    title: str
    raw: RawSheet
    subtitle: str = ""
    layout: Layout | None = None


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def safe_tab_name(name: str, taken: set[str]) -> str:
    """Excel tab names: <=31 chars, no []:*?/\\, must be unique."""
    clean = _INVALID_TAB_CHARS.sub("-", (name or "Sheet").strip()) or "Sheet"
    clean = clean[:31]
    candidate = clean
    n = 2
    while candidate.lower() in {t.lower() for t in taken}:
        suffix = f" ({n})"
        candidate = clean[: 31 - len(suffix)] + suffix
        n += 1
    taken.add(candidate)
    return candidate


def _write_sheet(ws: Worksheet, spec: SheetSpec, opts: ReportOptions, kit: S.StyleKit) -> None:
    raw = spec.raw
    layout = spec.layout or detect_layout(raw.rows)
    n_cols = raw.n_cols
    if n_cols == 0:
        ws["A1"] = "(no data returned for this view)"
        ws["A1"].font = kit.subtitle_font
        return

    header_block = raw.rows[: layout.header_rows]
    data_block = raw.rows[layout.header_rows :]

    # -- title block -------------------------------------------------------
    ws.cell(row=1, column=1, value=spec.title).font = kit.title_font
    ws.cell(row=1, column=1).alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = S.TITLE_ROW_HEIGHT

    report_date = opts.report_date or dt.date.today()
    if n_cols >= 4:
        date_col = max(1, n_cols - 1)
        if date_col > 1:
            ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=date_col - 1)
        dcell = ws.cell(row=1, column=date_col, value=report_date)
        dcell.font = kit.date_font
        dcell.number_format = S.TITLE_DATE_FMT
        dcell.alignment = Alignment(horizontal="right", vertical="center")
        if date_col < n_cols:
            ws.merge_cells(start_row=1, start_column=date_col, end_row=1, end_column=n_cols)
    else:
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=n_cols)

    subtitle = spec.subtitle or opts.subtitle
    if subtitle:
        sc = ws.cell(row=2, column=1, value=subtitle)
        sc.font = kit.subtitle_font
        sc.alignment = Alignment(horizontal="left", vertical="center")
        if n_cols > 1:
            ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=n_cols)
        spacer_row = 3
    else:
        spacer_row = 2
    ws.row_dimensions[spacer_row].height = S.SPACER_ROW_HEIGHT

    header_start = spacer_row + 1
    data_start = header_start + len(header_block)

    # -- header block ------------------------------------------------------
    group_fill = PatternFill("solid", fgColor=kit.theme.light80)
    group_font = Font(name=S.BODY_FONT, size=S.BODY_SIZE, bold=True, color=S.BLACK)

    for i, hrow in enumerate(header_block):
        excel_row = header_start + i
        is_last = i == len(header_block) - 1
        for c in range(n_cols):
            value = hrow[c] if c < len(hrow) else None
            cell = ws.cell(row=excel_row, column=c + 1, value=value)
            cell.font = kit.header_font if is_last else group_font
            cell.fill = kit.header_fill if is_last else group_fill
            cell.alignment = kit.header_align
            cell.border = kit.header_border
        # Tableau writes a group label once and leaves the cells under it
        # blank; merging those runs reproduces the banded header of the
        # reference workbook.
        if not is_last:
            c = 0
            while c < n_cols:
                if _is_blank(hrow[c] if c < len(hrow) else None):
                    c += 1
                    continue
                end = c + 1
                while end < n_cols and _is_blank(hrow[end] if end < len(hrow) else None):
                    end += 1
                if end - c > 1:
                    ws.merge_cells(start_row=excel_row, start_column=c + 1, end_row=excel_row, end_column=end)
                c = end
        ws.row_dimensions[excel_row].height = S.HEADER_ROW_HEIGHT if is_last else 18.0

    # -- per-column number formats ----------------------------------------
    col_formats: list[str | None] = []
    for c in range(n_cols):
        source = raw.col_formats[c] if c < len(raw.col_formats) else "General"
        values = [r[c] for r in data_block if c < len(r)]
        col_formats.append(None if c < layout.label_cols else _infer_format(values, source, kit))

    # -- data block --------------------------------------------------------
    for i, drow in enumerate(data_block):
        excel_row = data_start + i
        label = next(
            (str(drow[c]) for c in range(min(layout.label_cols, len(drow))) if not _is_blank(drow[c])),
            "",
        )
        is_grand = opts.highlight_totals and bool(_GRAND_TOTAL_RE.match(label))
        is_sub = opts.highlight_totals and not is_grand and bool(_SUBTOTAL_RE.search(label))
        band = opts.banded_rows and not (is_grand or is_sub) and i % 2 == 1

        for c in range(n_cols):
            value = drow[c] if c < len(drow) else None
            cell = ws.cell(row=excel_row, column=c + 1, value=value)
            cell.border = kit.cell_border
            if c < layout.label_cols:
                cell.alignment = kit.label_align
            else:
                cell.alignment = kit.value_align
                if col_formats[c]:
                    cell.number_format = col_formats[c]

            if is_grand:
                cell.font = kit.total_font
                cell.fill = kit.total_fill
            elif is_sub:
                cell.font = kit.subtotal_font
                cell.fill = kit.subtotal_fill
            else:
                cell.font = kit.body_font
                if band:
                    cell.fill = kit.band_fill

    last_row = data_start + len(data_block) - 1

    # -- column widths -----------------------------------------------------
    for c in range(n_cols):
        header_len = max(
            (_display_len(h[c] if c < len(h) else None, None) for h in header_block), default=0
        )
        body_len = max(
            (_display_len(r[c] if c < len(r) else None, col_formats[c]) for r in data_block),
            default=0,
        )
        # Headers wrap onto two lines, so they need far less width than their
        # full length; body text drives the width.
        width = max(body_len + 3, min(header_len, 18) + 3)
        floor = S.LABEL_COL_MIN_WIDTH if c < layout.label_cols else S.MIN_COL_WIDTH
        ws.column_dimensions[get_column_letter(c + 1)].width = max(
            floor, min(S.MAX_COL_WIDTH, width)
        )

    # -- sheet chrome ------------------------------------------------------
    ws.sheet_view.showGridLines = False
    ws.sheet_view.zoomScale = S.ZOOM
    ws.sheet_properties.tabColor = kit.theme.accent

    if opts.freeze_panes:
        ws.freeze_panes = ws.cell(row=data_start, column=layout.label_cols + 1)
    if opts.autofilter and len(header_block) and last_row >= data_start:
        header_row = header_start + len(header_block) - 1
        ws.auto_filter.ref = f"A{header_row}:{get_column_letter(n_cols)}{last_row}"

    ws.page_setup.orientation = "landscape" if opts.landscape else "portrait"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    if opts.fit_to_width:
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
    ws.print_title_rows = f"{header_start}:{header_start + len(header_block) - 1}"
    ws.print_options.horizontalCentered = True
    ws.oddFooter.left.text = spec.title
    ws.oddFooter.right.text = "Page &P of &N"
    ws.oddFooter.left.size = ws.oddFooter.right.size = 8
    ws.oddFooter.left.color = ws.oddFooter.right.color = S.SUBTITLE_TEXT


def _write_contents(ws: Worksheet, specs: Sequence[SheetSpec], opts: ReportOptions, kit: S.StyleKit) -> None:
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = kit.theme.accent

    ws.cell(row=1, column=1, value=opts.report_title).font = kit.title_font
    ws.row_dimensions[1].height = S.TITLE_ROW_HEIGHT
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=4)

    generated = dt.datetime.now().strftime("%d-%b-%Y %H:%M")
    sub = ws.cell(row=2, column=1, value=f"{opts.subtitle}  ·  generated {generated}".strip(" ·"))
    sub.font = kit.subtitle_font
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=4)
    ws.row_dimensions[3].height = S.SPACER_ROW_HEIGHT

    for c, label in enumerate(["#", "Worksheet", "Rows", "Columns"], start=1):
        cell = ws.cell(row=4, column=c, value=label)
        cell.font = kit.header_font
        cell.fill = kit.header_fill
        cell.alignment = kit.header_align
        cell.border = kit.header_border
    ws.row_dimensions[4].height = 20.0

    for i, spec in enumerate(specs, start=1):
        row = 4 + i
        layout = spec.layout or detect_layout(spec.raw.rows)
        values = [i, spec.title, max(0, spec.raw.n_rows - layout.header_rows), spec.raw.n_cols]
        for c, value in enumerate(values, start=1):
            cell = ws.cell(row=row, column=c, value=value)
            cell.font = kit.body_font
            cell.border = kit.cell_border
            cell.alignment = kit.label_align if c == 2 else kit.value_align
        link = ws.cell(row=row, column=2)
        link.hyperlink = f"#'{spec.tab_name}'!A1"
        link.font = Font(name=S.BODY_FONT, size=S.BODY_SIZE, color="0563C1", underline="single")

    for col, width in zip("ABCD", (6, 46, 12, 12)):
        ws.column_dimensions[col].width = width


def build_workbook(specs: Sequence[SheetSpec], opts: ReportOptions) -> openpyxl.Workbook:
    kit = opts.kit()
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    taken: set[str] = set()
    for spec in specs:
        spec.tab_name = safe_tab_name(spec.tab_name, taken)

    if opts.contents_sheet and len(specs) > 1:
        _write_contents(wb.create_sheet("Contents"), specs, opts, kit)

    for spec in specs:
        _write_sheet(wb.create_sheet(spec.tab_name), spec, opts, kit)

    if wb.sheetnames:
        wb.active = 0
    wb.properties.title = opts.report_title
    wb.properties.creator = "Tableau Report Mailer"
    return wb


def save_workbook(wb: openpyxl.Workbook, path: str) -> str:
    wb.save(path)
    return path


# ---------------------------------------------------------------------------
# HTML preview - mirrors the workbook styling so the app can show what will be
# sent without anyone having to open Excel.
# ---------------------------------------------------------------------------


def _fmt_preview(value: Any, number_format: str | None) -> str:
    if _is_blank(value):
        return ""
    if isinstance(value, (dt.datetime, dt.date)):
        return value.strftime("%d-%b-%Y")
    if _is_number(value) and number_format:
        if "%" in number_format:
            return f"{value * 100:,.2f}%"
        if "₹" in number_format:
            return f"₹ {value:,.2f}" if ".00" in number_format else f"₹ {value:,.0f}"
        if ".00" in number_format:
            return f"({abs(value):,.2f})" if value < 0 else f"{value:,.2f}"
        return f"({abs(value):,.0f})" if value < 0 else f"{value:,.0f}"
    return str(value)


def _esc(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br>")
    )


def preview_html(specs: Sequence[SheetSpec], opts: ReportOptions, max_rows: int = 60) -> str:
    """Render the report as HTML using the same palette as the workbook."""
    kit = opts.kit()
    t = kit.theme
    report_date = (opts.report_date or dt.date.today()).strftime("%d-%b-%Y")
    parts: list[str] = [
        f"""<style>
        .tm-wrap {{ font-family: Calibri, 'Segoe UI', sans-serif; color:#000; }}
        .tm-wrap table {{ border-collapse: collapse; width: max-content; min-width:100%;
                          font-size: 12.5px; background:#fff; }}
        .tm-wrap td, .tm-wrap th {{ border: 1px solid #{S.GRID_LINE}; padding: 3px 8px;
                                    white-space: nowrap; }}
        .tm-title {{ font-size: 18px; font-weight: 700; padding: 6px 0 0; }}
        .tm-date {{ font-size: 18px; font-weight: 700; float: right; }}
        .tm-sub {{ font-size: 11px; font-style: italic; color:#{S.SUBTITLE_TEXT};
                   padding-bottom: 8px; }}
        .tm-hdr {{ background:#{t.accent}; color:#fff; font-weight:700; text-align:center;
                   vertical-align:middle; }}
        .tm-grp {{ background:#{t.light80}; color:#000; font-weight:700; text-align:center; }}
        .tm-sub-row td {{ background:#{t.light60}; font-weight:700; }}
        .tm-tot-row td {{ background:#{S.GRAND_TOTAL_FILL}; font-weight:700; }}
        .tm-band td {{ background:#{S.BAND_FILL}; }}
        .tm-num {{ text-align: right; }}
        .tm-scroll {{ overflow-x: auto; border:1px solid #{S.GRID_LINE}; border-radius:6px;
                      padding: 12px 14px; margin-bottom: 22px; background:#fff; }}
        .tm-more {{ font-size:11px; color:#{S.SUBTITLE_TEXT}; padding-top:6px; }}
        </style>"""
    ]

    for spec in specs:
        raw = spec.raw
        layout = spec.layout or detect_layout(raw.rows)
        header_block = raw.rows[: layout.header_rows]
        data_block = raw.rows[layout.header_rows :]
        n_cols = raw.n_cols

        col_formats: list[str | None] = []
        for c in range(n_cols):
            source = raw.col_formats[c] if c < len(raw.col_formats) else "General"
            values = [r[c] for r in data_block if c < len(r)]
            col_formats.append(None if c < layout.label_cols else _infer_format(values, source, kit))

        parts.append('<div class="tm-wrap"><div class="tm-scroll">')
        parts.append(f'<div><span class="tm-date">{report_date}</span>')
        parts.append(f'<div class="tm-title">{_esc(spec.title)}</div></div>')
        if spec.subtitle or opts.subtitle:
            parts.append(f'<div class="tm-sub">{_esc(spec.subtitle or opts.subtitle)}</div>')
        parts.append("<table><thead>")
        for i, hrow in enumerate(header_block):
            is_last = i == len(header_block) - 1
            parts.append("<tr>")
            c = 0
            while c < n_cols:
                value = hrow[c] if c < len(hrow) else None
                span = 1
                if not is_last and not _is_blank(value):
                    end = c + 1
                    while end < n_cols and _is_blank(hrow[end] if end < len(hrow) else None):
                        end += 1
                    span = end - c
                cls = "tm-hdr" if is_last else "tm-grp"
                colspan = f' colspan="{span}"' if span > 1 else ""
                parts.append(f'<th class="{cls}"{colspan}>{_esc(value or "")}</th>')
                c += span
            parts.append("</tr>")
        parts.append("</thead><tbody>")

        for i, drow in enumerate(data_block[:max_rows]):
            label = next(
                (str(drow[c]) for c in range(min(layout.label_cols, len(drow))) if not _is_blank(drow[c])),
                "",
            )
            if opts.highlight_totals and _GRAND_TOTAL_RE.match(label):
                row_cls = "tm-tot-row"
            elif opts.highlight_totals and _SUBTOTAL_RE.search(label):
                row_cls = "tm-sub-row"
            elif opts.banded_rows and i % 2 == 1:
                row_cls = "tm-band"
            else:
                row_cls = ""
            parts.append(f'<tr class="{row_cls}">')
            for c in range(n_cols):
                value = drow[c] if c < len(drow) else None
                num = c >= layout.label_cols
                cls = ' class="tm-num"' if num else ""
                parts.append(f"<td{cls}>{_esc(_fmt_preview(value, col_formats[c]))}</td>")
            parts.append("</tr>")
        parts.append("</tbody></table>")
        if len(data_block) > max_rows:
            hidden = len(data_block) - max_rows
            parts.append(f'<div class="tm-more">+ {hidden:,} more rows in the Excel file</div>')
        parts.append("</div></div>")

    return "".join(parts)


def workbook_bytes(wb: openpyxl.Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
