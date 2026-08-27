"""Settings, saved templates, distribution lists and saved jobs.

Non-secret settings live in ``config/settings.json``.  Secrets (the Tableau
personal access token, mainly) live in ``.env`` so they never end up in git.
Both files are created on first save.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv, set_key

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
OUTPUT_DIR = ROOT / "output"
SETTINGS_PATH = CONFIG_DIR / "settings.json"
ENV_PATH = ROOT / ".env"
GMAIL_CLIENT_PATH = CONFIG_DIR / "gmail_credentials.json"
GMAIL_TOKEN_PATH = CONFIG_DIR / "gmail_token.json"
HISTORY_PATH = CONFIG_DIR / "history.jsonl"

CONFIG_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
load_dotenv(ENV_PATH)


# ---------------------------------------------------------------------------
# Server URL handling
# ---------------------------------------------------------------------------

_SITE_IN_URL = re.compile(r"/(?:#/)?(?:site|t)/([^/?#]+)", re.I)


def normalise_server_url(url: str) -> str:
    """Reduce any Tableau URL to just scheme://host.

    People paste the whole address bar - ``https://pod.online.tableau.com/#/site/acme/
    workbooks`` - but the REST client builds its endpoints by appending
    ``/api/<version>/...`` to whatever it is given.  Everything after ``#`` is a
    fragment, so an un-trimmed URL sends every API call to the site homepage,
    which answers with HTML.  The client then can't parse the version and every
    later call fails with "Invalid version: 'Unknown'".
    """
    url = (url or "").strip()
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url.lstrip("/")
    parsed = urlparse(url)
    if not parsed.netloc:
        return url.rstrip("/")
    return f"{parsed.scheme.lower()}://{parsed.netloc}"


def site_from_url(url: str) -> str:
    """Pull the site content URL out of a pasted Tableau address, if present.

    ``https://pod.online.tableau.com/#/site/acme/workbooks`` -> ``acme``.
    The Default site has no ``/site/`` segment, so this returns "".
    """
    match = _SITE_IN_URL.search(url or "")
    return match.group(1) if match else ""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class TableauSettings:
    server_url: str = ""  # e.g. https://10ay.online.tableau.com
    site_content_url: str = ""  # from /#/site/<this>/ - blank for Default
    token_name: str = ""
    verify_ssl: bool = True

    def __post_init__(self) -> None:
        # Repair anything already saved with a full browser URL.
        self.server_url = normalise_server_url(self.server_url)

    @property
    def token_secret(self) -> str:
        return os.environ.get("TABLEAU_TOKEN_SECRET", "")

    @property
    def is_configured(self) -> bool:
        return bool(self.server_url and self.token_name and self.token_secret)


@dataclass
class SubjectTemplate:
    name: str
    subject: str
    body: str = ""


@dataclass
class Recipients:
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)


@dataclass
class DistributionList:
    name: str
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)


@dataclass
class SavedJob:
    """Everything needed to reproduce a send, for one-click reruns."""

    name: str
    workbook_id: str = ""
    workbook_name: str = ""
    view_ids: list[str] = field(default_factory=list)
    view_names: list[str] = field(default_factory=list)
    content_mode: str = "crosstab"  # crosstab | dashboard | both
    dashboard_view_ids: list[str] = field(default_factory=list)
    one_file_per_view: bool = False
    subject: str = ""
    body: str = ""
    filename: str = "{workbook} - {today}.xlsx"
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    theme: str = "Blue"
    negatives_red: bool = False
    banded_rows: bool = False
    contents_sheet: bool = True
    autofilter: bool = True
    freeze_panes: bool = True
    filters: dict[str, str] = field(default_factory=dict)
    report_title: str = ""


DEFAULT_TEMPLATES = [
    SubjectTemplate(
        name="Daily sales report",
        subject="{workbook} - {today:%d.%m.%Y}",
        body=(
            "Hi all,\n\nPlease find attached the {workbook} for {today:%d %B %Y}.\n\n"
            "Sheets included: {views}\n\nRegards"
        ),
    ),
    SubjectTemplate(
        name="Yesterday's figures",
        subject="{workbook} - figures for {yesterday:%d %b %Y}",
        body="Hi all,\n\nAttached are the figures for {yesterday:%d %B %Y}.\n\nRegards",
    ),
    SubjectTemplate(
        name="Monthly report",
        subject="{workbook} - {month} {year}",
        body="Hi all,\n\nAttached is the {workbook} for {month} {year}.\n\nRegards",
    ),
]


@dataclass
class Settings:
    tableau: TableauSettings = field(default_factory=TableauSettings)
    templates: list[SubjectTemplate] = field(default_factory=lambda: list(DEFAULT_TEMPLATES))
    lists: list[DistributionList] = field(default_factory=list)
    jobs: list[SavedJob] = field(default_factory=list)
    default_theme: str = "Blue"
    output_dir: str = str(OUTPUT_DIR)
    sender_name: str = ""
    signature: str = ""
    keep_local_copies: bool = True


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------


def _from_dict(cls, data: dict[str, Any]):
    """Build a dataclass, ignoring keys it doesn't know about."""
    known = {f for f in cls.__dataclass_fields__}
    return cls(**{k: v for k, v in data.items() if k in known})


def load_settings() -> Settings:
    if not SETTINGS_PATH.exists():
        return Settings()
    try:
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return Settings()

    settings = Settings()
    settings.tableau = _from_dict(TableauSettings, raw.get("tableau", {}))
    templates = [_from_dict(SubjectTemplate, t) for t in raw.get("templates", [])]
    settings.templates = templates or list(DEFAULT_TEMPLATES)
    settings.lists = [_from_dict(DistributionList, d) for d in raw.get("lists", [])]
    settings.jobs = [_from_dict(SavedJob, j) for j in raw.get("jobs", [])]
    settings.default_theme = raw.get("default_theme", "Blue")
    settings.output_dir = raw.get("output_dir", str(OUTPUT_DIR))
    settings.sender_name = raw.get("sender_name", "")
    settings.signature = raw.get("signature", "")
    settings.keep_local_copies = raw.get("keep_local_copies", True)
    return settings


def save_settings(settings: Settings) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "tableau": asdict(settings.tableau),
        "templates": [asdict(t) for t in settings.templates],
        "lists": [asdict(d) for d in settings.lists],
        "jobs": [asdict(j) for j in settings.jobs],
        "default_theme": settings.default_theme,
        "output_dir": settings.output_dir,
        "sender_name": settings.sender_name,
        "signature": settings.signature,
        "keep_local_copies": settings.keep_local_copies,
    }
    SETTINGS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def save_token_secret(secret: str) -> None:
    """Write the Tableau PAT secret to .env (gitignored), not settings.json."""
    ENV_PATH.touch(exist_ok=True)
    set_key(str(ENV_PATH), "TABLEAU_TOKEN_SECRET", secret)
    os.environ["TABLEAU_TOKEN_SECRET"] = secret


def find_job(settings: Settings, name: str) -> SavedJob | None:
    return next((j for j in settings.jobs if j.name == name), None)


def upsert_job(settings: Settings, job: SavedJob) -> None:
    for i, existing in enumerate(settings.jobs):
        if existing.name == job.name:
            settings.jobs[i] = job
            return
    settings.jobs.append(job)


def upsert_template(settings: Settings, template: SubjectTemplate) -> None:
    for i, existing in enumerate(settings.templates):
        if existing.name == template.name:
            settings.templates[i] = template
            return
    settings.templates.append(template)


def upsert_list(settings: Settings, dist: DistributionList) -> None:
    for i, existing in enumerate(settings.lists):
        if existing.name == dist.name:
            settings.lists[i] = dist
            return
    settings.lists.append(dist)
