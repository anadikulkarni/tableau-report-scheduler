"""Gmail API sending via an OAuth desktop client.

Set-up, once:
  1. Google Cloud Console -> APIs & Services -> Enable the Gmail API
  2. Credentials -> Create credentials -> OAuth client ID -> Desktop app
  3. Download the JSON and save it as ``config/gmail_credentials.json``

The first send opens a browser to consent; the refresh token is then cached in
``config/gmail_token.json`` and reused.
"""

from __future__ import annotations

import base64
import mimetypes
import os
import re
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable, Sequence

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .config import GMAIL_CLIENT_PATH, GMAIL_TOKEN_PATH

# compose covers both creating drafts and sending.
SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class GmailError(RuntimeError):
    """A Gmail failure with a message worth showing the user."""


@dataclass
class Attachment:
    filename: str
    content: bytes
    mime_type: str = ""

    def resolved_mime(self) -> tuple[str, str]:
        mime = self.mime_type or mimetypes.guess_type(self.filename)[0] or "application/octet-stream"
        main, _, sub = mime.partition("/")
        return main, sub or "octet-stream"


@dataclass
class InlineImage:
    cid: str
    content: bytes
    subtype: str = "png"
    caption: str = ""


@dataclass
class BodyTable:
    """A crosstab rendered into the email itself rather than attached."""

    title: str
    html: str
    text: str


@dataclass
class Message:
    to: list[str]
    subject: str
    body: str
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)
    inline_images: list[InlineImage] = field(default_factory=list)
    body_tables: list[BodyTable] = field(default_factory=list)
    sender_name: str = ""
    reply_to: str = ""


# ---------------------------------------------------------------------------
# Address handling
# ---------------------------------------------------------------------------


def parse_addresses(text: str | Iterable[str]) -> list[str]:
    """Accept commas, semicolons, newlines or a list; return clean addresses."""
    if not isinstance(text, str):
        parts: list[str] = []
        for item in text:
            parts.extend(parse_addresses(item))
        return list(dict.fromkeys(parts))
    parts = [p.strip() for p in re.split(r"[,;\n]+", text)]
    return list(dict.fromkeys(p for p in parts if p))


def invalid_addresses(addresses: Sequence[str]) -> list[str]:
    return [a for a in addresses if not _EMAIL_RE.match(a)]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def env_credentials_present() -> bool:
    return all(
        os.environ.get(k)
        for k in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN")
    )


def client_secret_present() -> bool:
    return env_credentials_present() or GMAIL_CLIENT_PATH.exists()


def is_authorised() -> bool:
    return env_credentials_present() or GMAIL_TOKEN_PATH.exists()


def _credentials_from_env() -> Credentials | None:
    """Build credentials from a pre-issued refresh token.

    The desktop OAuth flow opens a browser on whatever machine runs the code,
    which is useless on a headless host like Streamlit Cloud. So a token minted
    once locally is carried in the environment (from st.secrets) instead.
    """
    client_id = os.environ.get("GMAIL_CLIENT_ID")
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET")
    refresh_token = os.environ.get("GMAIL_REFRESH_TOKEN")
    if not (client_id and client_secret and refresh_token):
        return None

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    try:
        creds.refresh(Request())
    except Exception as exc:  # noqa: BLE001
        raise GmailError(
            "The stored Gmail refresh token was rejected. Most likely the OAuth "
            "consent screen is an External app still in 'Testing', where tokens "
            "expire after 7 days - set it to Internal (Workspace) and mint a new "
            f"token. ({exc})"
        ) from exc
    return creds


def load_credentials(interactive: bool = True) -> Credentials:
    env_creds = _credentials_from_env()
    if env_creds is not None:
        return env_creds

    if not GMAIL_CLIENT_PATH.exists():
        raise GmailError(
            f"Missing OAuth client file. Save your Google Cloud desktop-app JSON as {GMAIL_CLIENT_PATH}."
        )

    creds: Credentials | None = None
    if GMAIL_TOKEN_PATH.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(GMAIL_TOKEN_PATH), SCOPES)
        except (ValueError, OSError):
            creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            GMAIL_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
            return creds
        except Exception:  # noqa: BLE001 - fall through to a fresh consent
            creds = None

    if not interactive:
        raise GmailError("Gmail is not authorised yet. Open the app and click 'Connect Gmail'.")

    flow = InstalledAppFlow.from_client_secrets_file(str(GMAIL_CLIENT_PATH), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    GMAIL_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _service(interactive: bool = True):
    try:
        return build("gmail", "v1", credentials=load_credentials(interactive), cache_discovery=False)
    except GmailError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise GmailError(f"Could not start the Gmail client: {exc}") from exc


def authorised_address(interactive: bool = True) -> str:
    try:
        profile = _service(interactive).users().getProfile(userId="me").execute()
        return profile.get("emailAddress", "")
    except HttpError as exc:
        raise GmailError(f"Gmail rejected the request: {exc}") from exc


def revoke_local_token() -> None:
    """Forget the cached token so the next send re-prompts for consent."""
    GMAIL_TOKEN_PATH.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Composing
# ---------------------------------------------------------------------------


TABLES_TOKEN = "{tables}"


def text_with_tables(body: str, tables: Sequence[BodyTable] = ()) -> str:
    """Plain-text body with the {tables} marker replaced by text tables."""
    rendered = "\n\n".join(t.text for t in tables if t.text)
    if TABLES_TOKEN in body:
        return body.replace(TABLES_TOKEN, rendered)
    return f"{body}\n\n{rendered}" if rendered else body


def text_to_html(
    body: str,
    inline_images: Sequence[InlineImage] = (),
    tables: Sequence[BodyTable] = (),
) -> str:
    # Split on the marker first so the tables are not HTML-escaped as text.
    segments = body.split(TABLES_TOKEN) if TABLES_TOKEN in body else [body]
    table_html = "".join(t.html for t in tables if t.html)

    def to_paragraphs(chunk: str) -> str:
        return "".join(
            f"<p style='margin:0 0 12px'>{line or '&nbsp;'}</p>"
            for line in (
                l.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                for l in chunk.split("\n")
            )
        )

    paragraphs = to_paragraphs(segments[0])
    for segment in segments[1:]:
        paragraphs += table_html + to_paragraphs(segment)
    if len(segments) == 1 and table_html:
        paragraphs += table_html

    images = ""
    for img in inline_images:
        caption = (
            f"<div style='font-size:12px;color:#7f7f7f;margin:4px 0 16px'>{img.caption}</div>"
            if img.caption
            else ""
        )
        images += (
            f"<div style='margin-top:18px'>"
            f"<img src='cid:{img.cid}' style='max-width:100%;border:1px solid #d9d9d9;border-radius:4px'>"
            f"{caption}</div>"
        )
    return (
        "<div style=\"font-family:Calibri,'Segoe UI',Arial,sans-serif;font-size:14px;"
        f'color:#1a1a1a;line-height:1.5">{paragraphs}{images}</div>'
    )


def build_message(msg: Message, sender: str = "") -> EmailMessage:
    email = EmailMessage()
    email["To"] = ", ".join(msg.to)
    if msg.cc:
        email["Cc"] = ", ".join(msg.cc)
    if msg.bcc:
        email["Bcc"] = ", ".join(msg.bcc)
    if sender:
        email["From"] = f"{msg.sender_name} <{sender}>" if msg.sender_name else sender
    if msg.reply_to:
        email["Reply-To"] = msg.reply_to
    email["Subject"] = msg.subject

    email.set_content(text_with_tables(msg.body or "", msg.body_tables))
    email.add_alternative(
        text_to_html(msg.body or "", msg.inline_images, msg.body_tables), subtype="html"
    )

    if msg.inline_images:
        html_part = email.get_payload()[-1]
        for img in msg.inline_images:
            html_part.add_related(
                img.content, maintype="image", subtype=img.subtype, cid=f"<{img.cid}>"
            )

    for att in msg.attachments:
        main, sub = att.resolved_mime()
        email.add_attachment(att.content, maintype=main, subtype=sub, filename=att.filename)

    return email


def _raw(email: EmailMessage) -> str:
    return base64.urlsafe_b64encode(email.as_bytes()).decode()


def send(msg: Message, interactive: bool = True) -> dict[str, str]:
    """Send the message. Returns the Gmail message id and thread id."""
    if not msg.to:
        raise GmailError("No recipients.")
    bad = invalid_addresses([*msg.to, *msg.cc, *msg.bcc])
    if bad:
        raise GmailError("These addresses don't look valid: " + ", ".join(bad))

    service = _service(interactive)
    sender = ""
    try:
        sender = service.users().getProfile(userId="me").execute().get("emailAddress", "")
    except HttpError:
        pass

    email = build_message(msg, sender)
    try:
        result = service.users().messages().send(userId="me", body={"raw": _raw(email)}).execute()
    except HttpError as exc:
        raise GmailError(f"Gmail refused to send: {exc}") from exc
    return {"id": result.get("id", ""), "thread_id": result.get("threadId", ""), "sender": sender}


def create_draft(msg: Message, interactive: bool = True) -> dict[str, str]:
    """Save the message as a Gmail draft instead of sending it."""
    service = _service(interactive)
    sender = ""
    try:
        sender = service.users().getProfile(userId="me").execute().get("emailAddress", "")
    except HttpError:
        pass
    email = build_message(msg, sender)
    try:
        result = (
            service.users()
            .drafts()
            .create(userId="me", body={"message": {"raw": _raw(email)}})
            .execute()
        )
    except HttpError as exc:
        raise GmailError(f"Gmail refused to save the draft: {exc}") from exc
    return {"id": result.get("id", ""), "sender": sender}


def save_client_secret(data: bytes) -> Path:
    GMAIL_CLIENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    GMAIL_CLIENT_PATH.write_bytes(data)
    return GMAIL_CLIENT_PATH
