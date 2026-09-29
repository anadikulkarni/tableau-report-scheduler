"""Create the shared-settings gist, once.

    set GITHUB_TOKEN=ghp_xxx          (Windows)   or  export GITHUB_TOKEN=...
    python tools/init_gist.py

Seeds the gist with whatever settings exist locally and prints the two lines to
paste into Streamlit secrets. Re-running with GITHUB_GIST_ID already set pushes
the local settings up instead of creating a second gist.

The token needs the **gist** scope only - it cannot touch your repositories.
Create one at https://github.com/settings/tokens (classic, tick `gist`).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tabmailer import store  # noqa: E402
from tabmailer.config import as_payload, load_settings  # noqa: E402


def main() -> int:
    if not os.environ.get("GITHUB_TOKEN"):
        print(
            "Set GITHUB_TOKEN first - a token with only the 'gist' scope.\n"
            "  https://github.com/settings/tokens",
            file=sys.stderr,
        )
        return 1

    try:
        who = store.whoami()
    except store.StoreError as exc:
        print(f"Token rejected: {exc}", file=sys.stderr)
        return 2
    print(f"Authenticated as {who}")

    settings = load_settings()
    payload = as_payload(settings, for_sharing=True)
    summary = (
        f"{len(payload['jobs'])} saved report(s), "
        f"{len(payload['templates'])} template(s), "
        f"{len(payload['lists'])} list(s)"
    )

    existing = os.environ.get("GITHUB_GIST_ID", "").strip()
    try:
        if existing:
            store.save(payload)
            gist_id = existing
            print(f"Updated existing gist with {summary}")
        else:
            gist_id = store.create(payload)
            print(f"Created a secret gist seeded with {summary}")
    except store.StoreError as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        return 3

    print(f"\n  https://gist.github.com/{gist_id}\n")
    print("Add these to .streamlit/secrets.toml and to Streamlit Cloud -> Settings -> Secrets:\n")
    print(f'GITHUB_TOKEN = "{os.environ["GITHUB_TOKEN"]}"')
    print(f'GITHUB_GIST_ID = "{gist_id}"')
    print(
        "\nNote: a secret gist is unlisted, not access-controlled - anyone with "
        "the link can read it. Keep report config and mailing lists in mind.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
