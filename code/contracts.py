"""Shared interface contract for M1.

This module is the ONLY file every M1 worker depends on. It is frozen for the
duration of M1 — if you believe it needs to change, say so rather than editing
it, because three parallel workers are building against it.

File ownership during M1 (do not touch files you do not own):
    code/data.py, code/media_cache.py   -> worker A (data layer)
    code/router.py, code/prompts.py     -> worker B (reasoning layer)
    code/writer.py, code/validate.py    -> worker C (output layer)
    code/contracts.py, code/main.py     -> integrator (main)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

# ─── Output contract (problem_statement.md §Output format) ──────────────────

OUTPUT_COLUMNS: tuple[str, ...] = (
    "message_id",
    "action",
    "message_type",
    "reason",
    "confidence",
    "evidence_message_ids",
)

ACTIONS: frozenset[str] = frozenset({"notify", "digest", "mute"})

MESSAGE_TYPES: frozenset[str] = frozenset({
    "personal", "urgent", "event", "payment", "business_update",
    "promotion", "greeting", "forward", "spam", "scam", "unknown",
})

# Observed in all 30 rows of sample_messages.csv. Style calibration only —
# see DECISIONS.md, this band is inferred from a small sample.
CONFIDENCE_MIN, CONFIDENCE_MAX = 0.78, 0.91

NO_EVIDENCE = "none"
EVIDENCE_SEPARATOR = ";"


# ─── Row types ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Message:
    """One row of dataset/messages.csv. Empty CSV cells become ''."""
    message_id: str
    user_id: str
    conversation_type: str          # personal | group | business
    group_id: str
    business_id: str
    sender_user_id: str
    created_at: str                 # "YYYY-MM-DD HH:MM"
    message_text: str               # NOTE: may be '' (e.g. msg_085)
    media_type: str                 # '' | image | voice
    media_id: str
    forwarded_count: int


@dataclass(frozen=True)
class MediaExtract:
    """One entry from code/cache/media.json, produced by M0.

    M0's on-disk schema is:
        {"<media_id>": {"text": str, "model": str, "error": str|None,
                        "problems": list, ...provider metadata}}
    Loaders MUST be tolerant: M0 is still running, so an entry may be
    missing entirely (voice notes are not written yet) or may carry a
    non-null `error`. Never raise on a missing media_id.
    """
    media_id: str
    text: str                       # '' when unavailable
    model: str = ""
    error: Optional[str] = None
    available: bool = True          # False => extraction missing or errored


@dataclass
class MessageContext:
    """Everything the reasoning layer is allowed to see for one message.

    Assembled by the data layer, consumed by the reasoning layer. Fields are
    plain dicts (raw CSV rows) so the reasoning layer can render them into a
    prompt without importing the data layer's internals.
    """
    message: Message
    media: Optional[MediaExtract] = None
    user: dict[str, Any] = field(default_factory=dict)
    group: dict[str, Any] = field(default_factory=dict)
    membership: dict[str, Any] = field(default_factory=dict)   # group_members row
    business: dict[str, Any] = field(default_factory=dict)
    business_history: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)  # this user's past messages
    events: dict[str, dict[str, Any]] = field(default_factory=dict)  # message_id -> event row
    notification_load: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Decision:
    """One routing decision. The writer turns this into an output.csv row."""
    message_id: str
    action: str                     # must be in ACTIONS
    message_type: str               # must be in MESSAGE_TYPES
    reason: str                     # single line, no newlines, no bare commas needed
    confidence: float               # [0.0, 1.0]
    evidence_message_ids: list[str] = field(default_factory=list)  # [] -> "none"


# ─── Provider selection ─────────────────────────────────────────────────────

RouterProvider = Literal["anthropic", "nvidia", "stub"]

#: `stub` is a deliberate, offline, deterministic heuristic router. It exists so
#: the M1 gate (a valid 110-row output.csv) can be met with no API key and no
#: network — M0 is still running and .env may be empty. It is NOT the real
#: router; M2/M3 replace its logic. Never delete it: it is also the fallback
#: that guarantees we always have a submittable output.csv.
DEFAULT_PROVIDER: RouterProvider = "stub"
