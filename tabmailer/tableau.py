"""Thin wrapper over tableauserverclient for the bits this app needs.

Authentication is by personal access token, which is the only option that works
reliably on Tableau Cloud once MFA is switched on.
"""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Mapping, Sequence

import tableauserverclient as TSC

from .config import TableauSettings


class TableauError(RuntimeError):
    """A Tableau failure with a message worth showing the user."""


@dataclass(frozen=True)
class WorkbookInfo:
    id: str
    name: str
    project: str
    updated_at: dt.datetime | None
    webpage_url: str = ""

    @property
    def label(self) -> str:
        return f"{self.name}  ·  {self.project}" if self.project else self.name


@dataclass(frozen=True)
class ViewInfo:
    id: str
    name: str
    sheet_type: str  # view | dashboard | story
    content_url: str = ""

    @property
    def is_dashboard(self) -> bool:
        return self.sheet_type == "dashboard"

    @property
    def is_story(self) -> bool:
        return self.sheet_type == "story"

    @property
    def label(self) -> str:
        icons = {"dashboard": "▦", "story": "▤", "view": "▧"}
        return f"{icons.get(self.sheet_type, '▧')}  {self.name}"


def _friendly(exc: Exception) -> TableauError:
    if isinstance(exc, TSC.ServerResponseError):
        code = getattr(exc, "code", "")
        detail = getattr(exc, "detail", "") or str(exc)
        hints = {
            "401002": "Sign-in failed. Check the token name, the token secret, and that the site is correct.",
            "401001": "Sign-in failed. The personal access token may have expired - tokens die after 15 days unused.",
            "404000": "Not found on this site. Check the site content URL.",
            "403004": "Your Tableau account does not have permission to export this view.",
        }
        hint = hints.get(str(code), "")
        return TableauError(f"{detail}{(' ' + hint) if hint else ''}".strip())
    return TableauError(str(exc))


class TableauClient:
    def __init__(self, settings: TableauSettings, max_age_minutes: int = 0) -> None:
        if not settings.server_url:
            raise TableauError("No Tableau server URL configured.")
        if not settings.token_name or not settings.token_secret:
            raise TableauError("No Tableau personal access token configured.")
        self.settings = settings
        self.max_age_minutes = max_age_minutes
        self.server = TSC.Server(settings.server_url.rstrip("/"), use_server_version=True)
        if not settings.verify_ssl:
            self.server.add_http_options({"verify": False})

    @contextmanager
    def session(self) -> Iterator[TSC.Server]:
        auth = TSC.PersonalAccessTokenAuth(
            self.settings.token_name,
            self.settings.token_secret,
            site_id=self.settings.site_content_url or "",
        )
        try:
            with self.server.auth.sign_in(auth):
                yield self.server
        except Exception as exc:  # noqa: BLE001 - re-raised with a better message
            raise _friendly(exc) from exc

    # -- discovery ---------------------------------------------------------

    def test_connection(self) -> dict[str, str]:
        with self.session() as server:
            site = server.sites.get_by_id(server.site_id)
            return {
                "site": site.name,
                "content_url": site.content_url or "(Default)",
                "api_version": server.version,
                "server": self.settings.server_url,
            }

    def list_workbooks(self) -> list[WorkbookInfo]:
        with self.session() as server:
            items = list(TSC.Pager(server.workbooks))
        books = [
            WorkbookInfo(
                id=w.id,
                name=w.name,
                project=w.project_name or "",
                updated_at=w.updated_at,
                webpage_url=w.webpage_url or "",
            )
            for w in items
        ]
        return sorted(books, key=lambda w: (w.project.lower(), w.name.lower()))

    def list_views(self, workbook_id: str) -> list[ViewInfo]:
        with self.session() as server:
            workbook = server.workbooks.get_by_id(workbook_id)
            server.workbooks.populate_views(workbook)
            views = list(workbook.views or [])
        return [
            ViewInfo(
                id=v.id,
                name=v.name,
                sheet_type=(v.sheet_type or "view").lower(),
                content_url=v.content_url or "",
            )
            for v in views
        ]

    # -- downloads ---------------------------------------------------------

    def _apply_filters(self, req, filters: Mapping[str, str] | None):
        for name, value in (filters or {}).items():
            if name and value != "":
                req.vf(name, str(value))
        return req

    def download_crosstab(self, view_id: str, filters: Mapping[str, str] | None = None) -> bytes:
        """Excel crosstab for one view (REST: /views/{id}/crosstab/excel)."""
        with self.session() as server:
            view = server.views.get_by_id(view_id)
            req = self._apply_filters(TSC.ExcelRequestOptions(maxage=self.max_age_minutes), filters)
            try:
                server.views.populate_excel(view, req)
                return b"".join(view.excel)
            except Exception as exc:  # noqa: BLE001
                raise _friendly(exc) from exc

    def download_pdf(
        self,
        view_id: str,
        filters: Mapping[str, str] | None = None,
        landscape: bool = True,
        page_type: str = "A4",
    ) -> bytes:
        with self.session() as server:
            view = server.views.get_by_id(view_id)
            req = TSC.PDFRequestOptions(
                page_type=getattr(TSC.PDFRequestOptions.PageType, page_type, TSC.PDFRequestOptions.PageType.A4),
                orientation=(
                    TSC.PDFRequestOptions.Orientation.Landscape
                    if landscape
                    else TSC.PDFRequestOptions.Orientation.Portrait
                ),
                maxage=self.max_age_minutes,
            )
            self._apply_filters(req, filters)
            try:
                server.views.populate_pdf(view, req)
                return view.pdf
            except Exception as exc:  # noqa: BLE001
                raise _friendly(exc) from exc

    def download_image(self, view_id: str, filters: Mapping[str, str] | None = None) -> bytes:
        with self.session() as server:
            view = server.views.get_by_id(view_id)
            req = TSC.ImageRequestOptions(
                imageresolution=TSC.ImageRequestOptions.Resolution.High,
                maxage=self.max_age_minutes,
            )
            self._apply_filters(req, filters)
            try:
                server.views.populate_image(view, req)
                return view.image
            except Exception as exc:  # noqa: BLE001
                raise _friendly(exc) from exc

    def download_many_crosstabs(
        self, view_ids: Sequence[str], filters: Mapping[str, str] | None = None, progress=None
    ) -> dict[str, bytes]:
        """Download several crosstabs over a single sign-in."""
        results: dict[str, bytes] = {}
        with self.session() as server:
            for i, view_id in enumerate(view_ids):
                view = server.views.get_by_id(view_id)
                if progress:
                    progress(i, len(view_ids), view.name)
                req = self._apply_filters(
                    TSC.ExcelRequestOptions(maxage=self.max_age_minutes), filters
                )
                try:
                    server.views.populate_excel(view, req)
                    results[view_id] = b"".join(view.excel)
                except Exception as exc:  # noqa: BLE001
                    raise TableauError(
                        f"Could not export '{view.name}' as a crosstab: {_friendly(exc)}"
                    ) from exc
        return results
