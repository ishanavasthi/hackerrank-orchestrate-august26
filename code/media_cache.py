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
        # M0's two halves disagree on the field name: the Gemini image pass
        # writes "text", the Groq voice pass writes "transcript". Reading only
        # "text" silently loaded all 13 voice notes as empty, so accept either.
        text = entry.get("text") or entry.get("transcript") or ""
        model = entry.get("model") or ""
        available = bool(text.strip()) and error is None

        # Some ASR output begins mid-sentence — the provider dropped the
        # opening audio. The row still routes, but on partial content, so it
        # is flagged and confidence is reduced downstream rather than the row
        # silently looking as well-grounded as any other.
        head = text.lstrip()[:1]
        truncated = bool(head) and (head.islower() or head in ",;")

        cache[media_id] = MediaExtract(
            media_id=media_id,
            text=text if available else "",
            model=model,
            error=error,
            available=available,
            truncated=truncated and available,
        )

    return cache
