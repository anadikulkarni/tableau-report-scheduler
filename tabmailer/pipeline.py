"""Download from Tableau, format to Excel, then hand it to Gmail.

Building and sending are deliberately separate so the app can show a preview of
exactly what will go out before anything is actually emailed.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from . import excel, gmail, history, templates
from .config import SavedJob, Settings
from .tableau import TableauClient, TableauError, ViewInfo

Progress = Callable[[str, float], None]


@dataclass
class OutputFile:
    filename: str
    content: bytes
    kind: str  # excel | pdf

    @property
    def size_kb(self) -> float:
        return len(self.content) / 1024


@dataclass
class BuildResult:
    specs: list[excel.SheetSpec] = field(default_factory=list)
    files: list[OutputFile] = field(default_factory=list)
    inline_images: list[gmail.InlineImage] = field(default_factory=list)
    body_tables: list[gmail.BodyTable] = field(default_factory=list)
    subject: str = ""
    body: str = ""
    context: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    saved_paths: list[str] = field(default_factory=list)

    @property
    def total_size_kb(self) -> float:
        return sum(f.size_kb for f in self.files)

    @property
    def has_content(self) -> bool:
        return bool(self.files or self.inline_images or self.body_tables)


def report_options(job: SavedJob, title: str, subtitle: str) -> excel.ReportOptions:
    return excel.ReportOptions(
        theme=job.theme,
        report_title=title,
        subtitle=subtitle,
        negatives_red=job.negatives_red,
        banded_rows=job.banded_rows,
        autofilter=job.autofilter,
        freeze_mode=job.freeze_mode,
    )


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-") or "view"


def _redundant_prefix(names: Sequence[str]) -> str:
    """The shared 'Workbook Name: ' prefix Tableau view names often carry.

    Every sheet in a workbook is commonly named ``Daily Sales Report: ASM Area
    Wise``. Repeating that on every tab wastes most of Excel's 31-character tab
    limit and truncates the part that actually distinguishes them, so it gets
    stripped when - and only when - it is common to all the selected views.
    """
    if len(names) < 2:
        return ""
    prefix = os.path.commonprefix([n for n in names])
    # Only cut at a separator, never mid-word.
    match = re.match(r"^(.*[:–—|-]\s*)", prefix)
    if not match:
        return ""
    candidate = match.group(1)
    # Never strip so much that a name would disappear.
    if any(not n[len(candidate):].strip() for n in names):
        return ""
    return candidate


def _short_name(name: str, prefix: str) -> str:
    return name[len(prefix):].strip() if prefix and name.startswith(prefix) else name


def build(
    job: SavedJob,
    settings: Settings,
    views: Sequence[ViewInfo],
    progress: Progress | None = None,
    today: dt.date | None = None,
) -> BuildResult:
    """Fetch everything the job asks for and format it. Sends nothing."""
    result = BuildResult()
    by_id = {v.id: v for v in views}
    by_name = {v.name: v for v in views}

    def resolve(ids: Sequence[str], names: Sequence[str]) -> list[ViewInfo]:
        """IDs first, falling back to names.

        Republishing a workbook mints new view ids, which would silently drop
        sheets from a saved job. Names survive that, so they are the backstop.
        """
        found: list[ViewInfo] = []
        seen: set[str] = set()
        for view_id in ids:
            view = by_id.get(view_id)
            if view and view.id not in seen:
                found.append(view)
                seen.add(view.id)
        if len(found) < len(ids):
            for name in names:
                view = by_name.get(name)
                if view and view.id not in seen:
                    found.append(view)
                    seen.add(view.id)
        return found

    crosstab_views = resolve(job.view_ids, job.view_names)
    dashboard_views = [by_id[i] for i in job.dashboard_view_ids if i in by_id]
    body_table_views = resolve(job.body_table_view_ids, job.body_table_view_names)

    # A view shown as a table in the email should not also become a tab.
    body_ids = {v.id for v in body_table_views}
    crosstab_views = [v for v in crosstab_views if v.id not in body_ids]

    view_names = [v.name for v in crosstab_views] or [v.name for v in dashboard_views]
    context = templates.build_context(
        workbook=job.workbook_name,
        views=view_names,
        site=settings.tableau.site_content_url or "Default",
        today=today,
    )
    result.context = context
    result.subject = templates.render(job.subject, context)
    body_main = templates.render(job.body, context)

    report_title = templates.render(job.report_title or job.workbook_name, context)
    client = TableauClient(settings.tableau)

    # One prefix for the whole job, so a view shown in the email body is named
    # the same way as one that became a tab.
    prefix = _redundant_prefix(
        [v.name for v in (*crosstab_views, *body_table_views, *dashboard_views)]
    )

    # -- tables that go inside the email body -------------------------------
    if body_table_views:
        opts = report_options(job, report_title, job.workbook_name)
        for view in body_table_views:
            if progress:
                progress(f"Building email table: {view.name}", 0.0)
            try:
                data = client.download_crosstab(view.id, job.filters)
            except TableauError as exc:
                result.warnings.append(f"Email table for '{view.name}' failed: {exc}")
                continue
            raws = excel.read_crosstab(data)
            if not raws:
                result.warnings.append(f"'{view.name}' returned no data for the email table.")
                continue
            short = _short_name(view.name, prefix)
            for raw in raws:
                spec = excel.SheetSpec(tab_name=short, title=short, raw=raw)
                result.body_tables.append(
                    gmail.BodyTable(
                        title=short,
                        html=excel.email_table_html(spec, opts, job.body_table_max_rows),
                        text=excel.email_table_text(spec, opts, job.body_table_max_rows),
                    )
                )

    # Tables land where the template puts {tables}; otherwise straight after the
    # message text but still above the signature.
    if result.body_tables and templates.TABLES_TOKEN not in body_main:
        body_main = f"{body_main}\n\n{templates.TABLES_TOKEN}"
    if settings.signature:
        body_main = f"{body_main}\n\n{templates.render(settings.signature, context)}"
    result.body = body_main

    # -- crosstabs ---------------------------------------------------------
    if crosstab_views:
        total = len(crosstab_views) + len(dashboard_views)
        specs: list[excel.SheetSpec] = []
        per_view_specs: list[tuple[ViewInfo, list[excel.SheetSpec]]] = []

        for i, view in enumerate(crosstab_views):
            if progress:
                progress(f"Downloading crosstab: {view.name}", i / max(total, 1))
            if view.is_story:
                result.warnings.append(
                    f"'{view.name}' is a story - Tableau cannot export stories as a crosstab, so it was skipped."
                )
                continue
            data = client.download_crosstab(view.id, job.filters)
            raws = excel.read_crosstab(data)
            if not raws:
                result.warnings.append(f"'{view.name}' returned no data and was skipped.")
                continue
            short = _short_name(view.name, prefix)
            local: list[excel.SheetSpec] = []
            for n, raw in enumerate(raws):
                # A dashboard crosstab comes back as one tab per worksheet.
                tab = short if len(raws) == 1 else f"{short} - {raw.name}"
                title = tab.upper()
                local.append(
                    excel.SheetSpec(
                        tab_name=tab,
                        title=title,
                        raw=raw,
                        subtitle=f"{job.workbook_name}  ·  {short}",
                    )
                )
            specs.extend(local)
            per_view_specs.append((view, local))

        result.specs = specs

        if specs and job.one_file_per_view:
            for view, local in per_view_specs:
                local_context = dict(context)
                local_context["view"] = view.name
                local_context["views"] = view.name
                opts = report_options(job, view.name.upper(), job.workbook_name)
                opts.report_date = today or dt.date.today()
                wb = excel.build_workbook(local, opts)
                name = templates.render_filename(job.filename, local_context)
                stem, ext = name.rsplit(".", 1)
                result.files.append(
                    OutputFile(f"{stem} - {_slug(view.name)}.{ext}", excel.workbook_bytes(wb), "excel")
                )
        elif specs:
            opts = report_options(job, report_title, job.workbook_name)
            opts.report_date = today or dt.date.today()
            wb = excel.build_workbook(specs, opts)
            result.files.append(
                OutputFile(templates.render_filename(job.filename, context), excel.workbook_bytes(wb), "excel")
            )

    # -- dashboards --------------------------------------------------------
    for i, view in enumerate(dashboard_views):
        base = len(crosstab_views)
        total = base + len(dashboard_views)
        if progress:
            progress(f"Rendering dashboard: {view.name}", (base + i) / max(total, 1))
        try:
            pdf = client.download_pdf(view.id, job.filters, landscape=True)
            result.files.append(OutputFile(f"{_slug(view.name)} - {context['today']}.pdf", pdf, "pdf"))
        except TableauError as exc:
            result.warnings.append(f"PDF for '{view.name}' failed: {exc}")
        try:
            png = client.download_image(view.id, job.filters)
            result.inline_images.append(
                gmail.InlineImage(cid=f"dash{i}", content=png, subtype="png", caption=view.name)
            )
        except TableauError as exc:
            result.warnings.append(f"Image for '{view.name}' failed: {exc}")

    # -- keep a local copy -------------------------------------------------
    if settings.keep_local_copies and result.files:
        out_dir = Path(settings.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for f in result.files:
            path = out_dir / f.filename
            path.write_bytes(f.content)
            result.saved_paths.append(str(path))

    if progress:
        progress("Ready", 1.0)
    return result


def send(
    result: BuildResult,
    job: SavedJob,
    settings: Settings,
    to: Sequence[str],
    cc: Sequence[str] = (),
    bcc: Sequence[str] = (),
    as_draft: bool = False,
) -> dict[str, str]:
    """Send (or draft) the built report and write a history entry."""
    message = gmail.Message(
        to=list(to),
        cc=list(cc),
        bcc=list(bcc),
        subject=result.subject,
        body=result.body,
        attachments=[gmail.Attachment(f.filename, f.content) for f in result.files],
        inline_images=result.inline_images,
        body_tables=result.body_tables,
        sender_name=settings.sender_name,
    )

    try:
        outcome = gmail.create_draft(message) if as_draft else gmail.send(message)
        status = "draft" if as_draft else "sent"
        error = ""
    except gmail.GmailError as exc:
        outcome, status, error = {}, "failed", str(exc)

    history.record(
        {
            "job": job.name,
            "workbook": job.workbook_name,
            "views": job.view_names,
            "subject": result.subject,
            "to": list(to),
            "cc": list(cc),
            "bcc": list(bcc),
            "files": [f.filename for f in result.files],
            "size_kb": round(result.total_size_kb, 1),
            "status": status,
            "error": error,
            "message_id": outcome.get("id", ""),
        }
    )

    if error:
        raise gmail.GmailError(error)
    return outcome
