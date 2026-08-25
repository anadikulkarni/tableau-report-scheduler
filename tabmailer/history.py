"""Append-only log of everything the app has sent."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from .config import HISTORY_PATH


def record(entry: dict[str, Any]) -> None:
    entry = {"timestamp": dt.datetime.now().isoformat(timespec="seconds"), **entry}
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, default=str) + "\n")


def recent(limit: int = 50) -> list[dict[str, Any]]:
    if not HISTORY_PATH.exists():
        return []
    entries: list[dict[str, Any]] = []
    with HISTORY_PATH.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return list(reversed(entries))[:limit]
