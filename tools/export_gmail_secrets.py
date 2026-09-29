"""Print the Gmail values to paste into Streamlit secrets.

Run this after connecting Gmail in the app locally:

    python tools/export_gmail_secrets.py

It reads config/gmail_token.json (and falls back to config/gmail_credentials.json
for the client details) and prints a TOML block. Nothing is sent anywhere.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKEN = ROOT / "config" / "gmail_token.json"
CLIENT = ROOT / "config" / "gmail_credentials.json"


def main() -> int:
    if not TOKEN.exists():
        print(
            f"No {TOKEN.name} yet. Open the app, connect Gmail, then run this again.",
            file=sys.stderr,
        )
        return 1

    token = json.loads(TOKEN.read_text(encoding="utf-8"))
    client_id = token.get("client_id")
    client_secret = token.get("client_secret")
    refresh_token = token.get("refresh_token")

    if (not client_id or not client_secret) and CLIENT.exists():
        installed = json.loads(CLIENT.read_text(encoding="utf-8"))
        inner = installed.get("installed") or installed.get("web") or {}
        client_id = client_id or inner.get("client_id")
        client_secret = client_secret or inner.get("client_secret")

    if not refresh_token:
        print(
            "That token file has no refresh_token. Disconnect Gmail in the app and "
            "reconnect - the consent screen must be shown to issue one.",
            file=sys.stderr,
        )
        return 2

    print("# Paste into Streamlit Cloud -> Settings -> Secrets")
    print(f'GMAIL_CLIENT_ID = "{client_id}"')
    print(f'GMAIL_CLIENT_SECRET = "{client_secret}"')
    print(f'GMAIL_REFRESH_TOKEN = "{refresh_token}"')
    print()
    print(
        "# Reminder: if the Google OAuth consent screen is still in 'Testing',\n"
        "# this refresh token stops working after 7 days. Publish the app first.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
