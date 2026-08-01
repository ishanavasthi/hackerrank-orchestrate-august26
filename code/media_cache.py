"""Tolerant loader for code/cache/media.json, produced by milestone M0.

M0 runs in a separate worktree and may still be writing entries when this is
called — right now the cache has the 20 image entries but not the 13
voice-note entries. This loader must never raise: a missing file, a missing
media_id, a null/missing text field, and a non-null error field are all the
normal case today, not edge cases. Each resolves to
MediaExtract(available=False, text="").
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Union

try:
    from code.contracts import MediaExtract
except ImportError:
    from contracts import MediaExtract


def load_media_cache(path: Union[str, Path]) -> dict[str, MediaExtract]:
    """Load code/cache/media.json into media_id -> MediaExtract. Never raises."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}

    if not isinstance(raw, dict):
        return {}

    cache: dict[str, MediaExtract] = {}
    for media_id, entry in raw.items():
        if not isinstance(entry, dict):
            cache[media_id] = MediaExtract(media_id=media_id, text="", available=False)
            continue

        error = entry.get("error")
        text = entry.get("text") or ""
        model = entry.get("model") or ""
        available = bool(text) and error is None

        cache[media_id] = MediaExtract(
            media_id=media_id,
            text=text if available else "",
            model=model,
            error=error,
            available=available,
        )

    return cache
