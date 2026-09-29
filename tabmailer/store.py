"""Shared settings storage backed by a GitHub gist.

Streamlit Cloud rebuilds its container on every restart, so anything written to
disk there is gone by morning. A secret gist gives every instance of the app one
small shared document to read and write, with no database to run.

Why a gist and not the repo itself: Streamlit Cloud redeploys whenever the
tracked branch is pushed, so committing settings would restart the app for
everyone each time somebody saved a template.

Configure with two environment variables (supplied from st.secrets in the app):

    GITHUB_TOKEN     a token with only the `gist` scope
    GITHUB_GIST_ID   the id of the gist, created by tools/init_gist.py

Without them the app falls back to local files and nothing here runs.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import requests

API = "https://api.github.com"
FILENAME = "tabmailer-settings.json"
TIMEOUT = 15


class StoreError(RuntimeError):
    """A storage failure worth showing the user."""


@dataclass(frozen=True)
class StoreInfo:
    kind: str  # "gist" | "local"
    label: str
    writable: bool
    detail: str = ""


def _token() -> str:
    return os.environ.get("GITHUB_TOKEN", "").strip()


def _gist_id() -> str:
    return os.environ.get("GITHUB_GIST_ID", "").strip()


def is_configured() -> bool:
    return bool(_token() and _gist_id())


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _explain(response: requests.Response) -> str:
    hints = {
        401: "the GitHub token is invalid or expired",
        403: "the token lacks the 'gist' scope",
        404: "no gist with that id is visible to this token",
    }
    hint = hints.get(response.status_code, "")
    return f"GitHub returned {response.status_code}{f' - {hint}' if hint else ''}"


# ---------------------------------------------------------------------------
# Read / write
# ---------------------------------------------------------------------------


def load() -> dict[str, Any] | None:
    """Fetch the shared settings blob, or None when storage isn't configured."""
    if not is_configured():
        return None
    try:
        response = requests.get(f"{API}/gists/{_gist_id()}", headers=_headers(), timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise StoreError(f"Could not reach GitHub: {exc}") from exc
    if not response.ok:
        raise StoreError(_explain(response))

    files = response.json().get("files", {})
    entry = files.get(FILENAME) or next(iter(files.values()), None)
    if not entry:
        return {}

    content = entry.get("content")
    if entry.get("truncated") or content is None:
        # Gists over ~1 MB come back truncated; fetch the raw blob instead.
        raw_url = entry.get("raw_url")
        if not raw_url:
            raise StoreError("The stored settings came back truncated.")
        try:
            raw = requests.get(raw_url, headers=_headers(), timeout=TIMEOUT)
            raw.raise_for_status()
            content = raw.text
        except requests.RequestException as exc:
            raise StoreError(f"Could not read the stored settings: {exc}") from exc

    if not content.strip():
        return {}
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise StoreError(f"The stored settings are not valid JSON: {exc}") from exc


def save(payload: dict[str, Any]) -> None:
    """Replace the shared settings blob."""
    if not is_configured():
        raise StoreError("Shared storage is not configured.")
    body = {"files": {FILENAME: {"content": json.dumps(payload, indent=2)}}}
    try:
        response = requests.patch(
            f"{API}/gists/{_gist_id()}", headers=_headers(), json=body, timeout=TIMEOUT
        )
    except requests.RequestException as exc:
        raise StoreError(f"Could not reach GitHub: {exc}") from exc
    if not response.ok:
        raise StoreError(_explain(response))


def create(payload: dict[str, Any], description: str = "Tableau Report Mailer settings") -> str:
    """Create the gist and return its id. Used once, by tools/init_gist.py."""
    if not _token():
        raise StoreError("Set GITHUB_TOKEN first.")
    body = {
        "description": description,
        "public": False,
        "files": {FILENAME: {"content": json.dumps(payload, indent=2)}},
    }
    try:
        response = requests.post(f"{API}/gists", headers=_headers(), json=body, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise StoreError(f"Could not reach GitHub: {exc}") from exc
    if not response.ok:
        raise StoreError(_explain(response))
    return response.json()["id"]


def whoami() -> str:
    """The GitHub login the token belongs to, for the setup script."""
    response = requests.get(f"{API}/user", headers=_headers(), timeout=TIMEOUT)
    if not response.ok:
        raise StoreError(_explain(response))
    return response.json().get("login", "?")
