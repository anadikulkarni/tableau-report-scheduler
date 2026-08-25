"""Placeholder rendering for subjects, bodies and file names.

Anything in braces is substituted, and date tokens accept a strftime spec after
a colon::

    {workbook} - {today:%d.%m.%Y}   ->  Sale Report - 25.08.2026
    Figures for {yesterday:%d %B}   ->  Figures for 24 August

Unknown placeholders are left alone rather than blowing up mid-send.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
import string
from typing import Any, Iterable, Mapping

DEFAULT_DATE_FMT = "%d.%m.%Y"
_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class _Date:
    """A date that formats as dd.mm.yyyy by default but honours strftime specs."""

    __slots__ = ("value", "default")

    def __init__(self, value: dt.date, default: str = DEFAULT_DATE_FMT) -> None:
        self.value = value
        self.default = default

    def __format__(self, spec: str) -> str:
        return self.value.strftime(spec or self.default)

    def __str__(self) -> str:
        return format(self, "")


class _Keep(dict):
    """Leaves unknown placeholders in place instead of raising."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class _SafeFormatter(string.Formatter):
    def get_value(self, key, args, kwargs):
        if isinstance(key, str):
            return kwargs[key]
        return super().get_value(key, args, kwargs)

    def format_field(self, value: Any, spec: str) -> str:
        try:
            return super().format_field(value, spec)
        except (ValueError, TypeError):
            return str(value)


_FORMATTER = _SafeFormatter()

# Tokens shown in the app's help panel.
TOKEN_HELP: list[tuple[str, str]] = [
    ("{today}", "Today, 25.08.2026 by default"),
    ("{today:%d %B %Y}", "Any strftime format, e.g. 25 August 2026"),
    ("{yesterday}", "Yesterday's date"),
    ("{tomorrow}", "Tomorrow's date"),
    ("{now:%H:%M}", "Current time"),
    ("{month}", "Current month name, e.g. August"),
    ("{month_short}", "Abbreviated month, e.g. Aug"),
    ("{prev_month}", "Previous month name"),
    ("{month_end}", "Last day of the current month"),
    ("{year}", "Calendar year, e.g. 2026"),
    ("{quarter}", "Calendar quarter, e.g. Q3"),
    ("{fy}", "Indian financial year, e.g. FY26-27"),
    ("{week}", "ISO week number"),
    ("{workbook}", "Selected Tableau workbook name"),
    ("{view}", "First selected worksheet name"),
    ("{views}", "Comma-separated list of selected worksheets"),
    ("{view_count}", "How many worksheets are included"),
    ("{site}", "Tableau site name"),
]


def indian_fy(date: dt.date) -> str:
    """Financial year running April to March, written FY26-27."""
    start = date.year if date.month >= 4 else date.year - 1
    return f"FY{start % 100:02d}-{(start + 1) % 100:02d}"


def build_context(
    *,
    workbook: str = "",
    views: Iterable[str] = (),
    site: str = "",
    today: dt.date | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    today = today or dt.date.today()
    now = dt.datetime.now()
    view_list = [v for v in views]
    first_of_month = today.replace(day=1)
    prev_month_end = first_of_month - dt.timedelta(days=1)
    last_day = calendar.monthrange(today.year, today.month)[1]

    context: dict[str, Any] = {
        "today": _Date(today),
        "yesterday": _Date(today - dt.timedelta(days=1)),
        "tomorrow": _Date(today + dt.timedelta(days=1)),
        "now": _Date(now, "%d.%m.%Y %H:%M"),
        "month": today.strftime("%B"),
        "month_short": today.strftime("%b"),
        "prev_month": prev_month_end.strftime("%B"),
        "month_start": _Date(first_of_month),
        "month_end": _Date(today.replace(day=last_day)),
        "year": today.year,
        "quarter": f"Q{(today.month - 1) // 3 + 1}",
        "fy": indian_fy(today),
        "week": f"{today.isocalendar().week:02d}",
        "workbook": workbook,
        "view": view_list[0] if view_list else "",
        "views": ", ".join(view_list),
        "view_count": len(view_list),
        "site": site,
    }
    if extra:
        context.update(extra)
    return context


def render(template: str, context: Mapping[str, Any]) -> str:
    if not template:
        return ""
    try:
        return _FORMATTER.vformat(template, (), _Keep(context))
    except (IndexError, KeyError, ValueError):
        # A malformed template should never stop a send - show it verbatim.
        return template


def render_filename(template: str, context: Mapping[str, Any], default_ext: str = ".xlsx") -> str:
    name = render(template, context).strip()
    if not name:
        name = "report"
    name = _INVALID_FILENAME.sub("-", name).strip(" .")
    if not name.lower().endswith(default_ext):
        name = f"{name}{default_ext}"
    return name[:180]


def preview_tokens(context: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """(token, description, rendered value) for the app's help table."""
    out = []
    for token, desc in TOKEN_HELP:
        out.append((token, desc, render(token, context)))
    return out
