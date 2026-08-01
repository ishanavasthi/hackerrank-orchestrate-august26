"""M3 — personalization.

Decides notify / digest / mute-for-low-value for the messages that cleared the
blind safety gate. Unlike the gate, this stage CAN see everything: engagement
history, mute state, promotion consent, DND windows, notification load. That
asymmetry is the whole design — see DECISIONS.md.

Two rules govern the ordering here:
  * This stage never emits `scam` or `spam`. Risk has exactly one owner and it
    is code/safety.py, which has already run.
  * A `mute` from this stage means "low value for this user", not "unsafe".
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

try:
    from code.contracts import Decision, MessageContext
except ImportError:
    from contracts import Decision, MessageContext


# ─── Content shape ──────────────────────────────────────────────────────────

# Urgency must be ANCHORED to an action or a deadline. Bare "now"/"today"
# are far too weak: "Smile today, stay blessed" (msg_011) and "Don't call now
# ... Nothing urgent" (msg_097) both matched and were wrongly read as urgent.
# Note "Nothing urgent" also matches a bare \burgent — the same negation trap
# the safety gate hit with "no OTP is required"; _NO_REPLY_NEEDED disarms it.
_URGENT = (
    # an action the sender wants taken immediately
    r"\b(?:call|come|join|reply|respond|confirm|send|check|collect|pay|dial|"
    r"approve|review)\w*\b[^.!?]{0,30}\b(?:now|immediately|asap|right away)\b",
    r"\b(?:now|immediately|asap|right away)\b[^.!?]{0,30}\b(?:call|come|join|reply|respond|confirm)\w*\b",
    r"call me", r"come online", r"stay online", r"join the (?:call|bridge|meeting)",
    r"\burgent(?:ly)?\b", r"\basap\b", r"immediately",
    # a real deadline, not a bare date word
    r"\b(?:by|before|due|expires?|closes?|ends?|last day)\b[^.!?]{0,25}"
    r"\b(?:today|tonight|noon|midnight|\d{1,2}\s?(?:am|pm|:\d{2}))\b",
    r"within \d+ (?:min|hour)", r"\bdeadline\b",
    # a change to an existing plan the recipient is party to
    r"moved to", r"rescheduled", r"changed to", r"shifted to", r"postponed",
    # something is broken
    r"is down", r"\bfailing\b", r"\bincident\b", r"\boutage\b", r"rollback",
    r"waiting for you", r"as soon as (?:possible|you)",
)
_NO_REPLY_NEEDED = (
    r"no need to reply", r"nothing urgent", r"not urgent", r"no rush",
    r"whenever you", r"don'?t call", r"talk (?:to|tomorrow|later)",
    r"nothing blocking",
)
_CHAIN = (
    r"share (?:this|it)? ?with (?:\d+|someone|everyone|all|others|ten|10)",
    r"forward (?:this )?to (?:\d+|ten|everyone|all)",
    r"send to \d+ people", r"before sunset", r"blessing", r"good luck",
    r"luck (?:will|comes)", r"do not ignore", r"copy paste",
    r"forwarded:", r"forwarding because", r"doctors don'?t",
    r"health (?:tip|secret)", r"positive energy",
)
_PROMO = (
    r"\boffer\b", r"\bdeal\b", r"\bdiscount", r"\bsale\b", r"% off",
    r"limited (?:time|period)", r"shop now", r"buy now", r"coupon",
    r"cashback", r"lowest price", r"book now", r"save (?:up to|flat)",
    r"exclusive", r"launch", r"new arrival",
)
_GREETING = (
    r"good morning", r"good night", r"happy (?:birthday|anniversary|new year)",
    r"shubh", r"bhagwan", r"stay blessed", r"have a (?:good|great) day",
)
_EVENT = (r"\bmeeting\b", r"\bsync\b", r"practice", r"class", r"trip",
          r"appointment", r"\brsvp\b", r"\bagenda\b", r"\bschedule")
_PAYMENT = (r"\bpayment\b", r"\binvoice\b", r"\bbill\b", r"\bdue\b", r"\bfee\b",
            r"\brefund\b", r"\bpaid\b", r"\btransaction\b", r"\bemi\b")
_TRANSACTIONAL = (r"deliver", r"order", r"shipment", r"booking", r"reserv",
                  r"appointment", r"ticket", r"statement", r"reminder")


def _hit(patterns, text: str) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


# ─── Signals ────────────────────────────────────────────────────────────────

@dataclass
class Signals:
    direct_mention: bool = False
    group_muted: bool = False
    group_dismissal_rate: Optional[float] = None
    group_disengaged: bool = False
    is_chain: bool = False
    urgent: bool = False
    defuses_urgency: bool = False
    promo: bool = False
    promo_unwanted: bool = False
    relationship_stale: bool = False
    relationship_active: bool = False
    in_dnd: bool = False
    load_high: bool = False
    unknown_sender: bool = False
    heavily_forwarded: bool = False
    notes: list[str] = field(default_factory=list)


def _num(v, default=None):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return default


def _in_dnd(created_at: str, window: str) -> bool:
    if not window or "-" not in window:
        return False
    try:
        t = datetime.strptime(created_at, "%Y-%m-%d %H:%M")
        a, b = window.split("-")
        ah, am = map(int, a.split(":"))
        bh, bm = map(int, b.split(":"))
    except (ValueError, TypeError):
        return False
    mins, start, end = t.hour * 60 + t.minute, ah * 60 + am, bh * 60 + bm
    return (mins >= start or mins < end) if start > end else (start <= mins < end)


def signals_for(ctx: MessageContext) -> Signals:
    m = ctx.message
    text = f"{m.message_text}\n{ctx.media.text if (ctx.media and ctx.media.available) else ''}"
    s = Signals()

    s.urgent = _hit(_URGENT, text)
    s.defuses_urgency = _hit(_NO_REPLY_NEEDED, text)
    s.is_chain = _hit(_CHAIN, text)
    s.promo = _hit(_PROMO, text)
    s.heavily_forwarded = (m.forwarded_count or 0) >= 5
    if s.heavily_forwarded:
        s.notes.append(f"forwarded {m.forwarded_count} times")

    # Direct mention of THIS recipient, not just any @mention.
    s.direct_mention = bool(re.search(rf"@{re.escape(m.user_id)}\b", m.message_text))
    if s.direct_mention:
        s.notes.append("names the recipient directly")

    # Group standing
    if ctx.membership:
        s.group_muted = str(ctx.membership.get("group_muted_by_user", "0")).strip() == "1"
        read = _num(ctx.membership.get("messages_read_30d"), 0) or 0
        dis = _num(ctx.membership.get("notifications_dismissed_30d"), 0) or 0
        if read + dis > 0:
            s.group_dismissal_rate = dis / (read + dis)
            s.group_disengaged = s.group_dismissal_rate >= 0.5
        if s.group_muted:
            s.notes.append("user has muted this group")
        elif s.group_disengaged:
            s.notes.append(f"user dismisses {s.group_dismissal_rate:.0%} of this group's notifications")

    # Business relationship and promotion consent
    if ctx.business_history:
        bh = ctx.business_history
        allows = str(bh.get("allows_promotions", "")).strip()
        opted_out = bool(str(bh.get("promotions_opted_out_at", "")).strip())
        why = str(bh.get("why_user_knows_account", ""))
        s.promo_unwanted = allows == "0" or opted_out
        s.relationship_stale = why.startswith("old_") or "opted_out" in why or "ignored" in why
        s.relationship_active = why.startswith(("active_", "recent_", "frequent_", "upcoming_",
                                                "confirmed_")) or "today" in why
        if opted_out:
            s.notes.append("user opted out of promotions from this sender")
        elif allows == "0":
            s.notes.append("user does not accept promotions from this sender")
        if s.relationship_stale:
            s.notes.append(f"relationship is stale ({why})")
    elif m.conversation_type == "personal" and not ctx.history:
        s.unknown_sender = True
        s.notes.append("no prior history with this sender")

    # Quiet hours — a tie-breaker only, per DECISIONS.md
    s.in_dnd = _in_dnd(m.created_at, str(ctx.user.get("do_not_disturb_window", "")))
    if s.in_dnd:
        s.notes.append("arrived inside the user's quiet hours")

    # Notification load, measured against this user's own norm rather than a
    # global constant — 7/day is heavy for a 2/day user and light for a 12/day one.
    loads = [_num(r.get("notifications_sent"), 0) or 0 for r in ctx.notification_load]
    if len(loads) >= 5:
        med = statistics.median(loads)
        recent = loads[-3:]
        if med > 0 and statistics.mean(recent) > med * 1.5:
            s.load_high = True
            s.notes.append("notification volume is already above this user's normal")

    return s


# ─── Type classification ────────────────────────────────────────────────────

def classify_type(ctx: MessageContext, s: Signals) -> str:
    m = ctx.message
    text = f"{m.message_text}\n{ctx.media.text if (ctx.media and ctx.media.available) else ''}"
    if not text.strip():
        return "unknown"
    # The sender's own defusing language ("nothing urgent", "no need to
    # reply") beats a keyword hit everywhere, not just in the urgent branch:
    # msg_033 is "Good morning beta ... Call me later when free, nothing
    # urgent" and must read as a greeting, not as urgent.
    really_urgent = s.urgent and not s.defuses_urgency

    if s.is_chain or s.heavily_forwarded:
        return "forward"
    if _hit(_GREETING, text) and not really_urgent:
        return "greeting"
    if _hit(_PAYMENT, text):
        return "payment"
    if s.promo:
        return "promotion"
    if _hit(_EVENT, text):
        return "event"
    if really_urgent and m.conversation_type != "business":
        return "urgent"
    if m.conversation_type == "business":
        return "business_update" if _hit(_TRANSACTIONAL, text) else "promotion"
    if m.conversation_type == "personal":
        return "personal"
    return "unknown"


# ─── Decision ───────────────────────────────────────────────────────────────

def _reason(parts: list[str]) -> str:
    txt = "; ".join(p for p in parts if p)
    return re.sub(r"\s+", " ", txt).strip()[:300]


def personalize(ctx: MessageContext) -> Decision:
    m = ctx.message
    s = signals_for(ctx)
    mtype = classify_type(ctx, s)
    why: list[str] = []

    # 1. Chain letters and mass forwards are low value regardless of source.
    #    This is the msg_040 case: a direct mention does not rescue a chain letter.
    if s.is_chain or (s.heavily_forwarded and not s.urgent):
        action = "mute"
        why.append("mass-forwarded chain content with no personal relevance")
        why += s.notes[:1]

    # 2. The spec's carve-out: a muted group can still carry an urgent direct
    #    mention (msg_056 — "@u_001 doctor appointment moved to 6 PM").
    elif s.direct_mention and s.urgent:
        action = "notify"
        why.append("directly addresses the recipient with time-sensitive information")
        if s.group_muted:
            why.append("overrides the group mute because it names the user specifically")

    # 3. Unwanted promotions. Consent is explicit in the data, so honour it.
    elif s.promo and s.promo_unwanted:
        action = "mute"
        why.append("promotional message from a sender the user does not accept promotions from")
        why += s.notes[:1]

    # 4. Muted group, nothing addressed to the user.
    elif s.group_muted:
        action = "mute" if not s.urgent else "digest"
        why.append("user has muted this group and the message is not addressed to them")

    # 5. Disengaged group — the user reads little and dismisses most of it.
    elif s.group_disengaged and not s.urgent:
        action = "digest"
        why.append(f"user dismisses most notifications from this group")

    # 6. Stale business relationship.
    elif s.relationship_stale and not s.urgent:
        action = "digest" if not s.promo else "mute"
        why.append("dormant relationship with this sender")

    # 7. Genuinely urgent, and the sender is not disqualified.
    elif s.urgent and not s.defuses_urgency:
        action = "notify"
        why.append("time-sensitive and expects a response now")

    # 8. Explicitly not urgent by the sender's own words (msg_101, msg_097).
    elif s.defuses_urgency:
        action = "digest"
        why.append("sender explicitly states no immediate response is needed")

    elif s.promo:
        action = "digest"
        why.append("promotional but the user accepts promotions from this sender")

    elif s.unknown_sender:
        action = "digest"
        why.append("first contact from an unknown sender, no prior history")

    elif m.conversation_type == "personal":
        action = "notify"
        why.append("direct personal message")

    else:
        action = "digest"
        why.append("useful context but not time-critical")

    # ── Modifiers. These only ever move a decision down, never up. ──

    # DND: tie-breaker, with the urgency carve-out. See DECISIONS.md — the
    # historical data does not support a strong rule, so it only demotes a
    # notify that is not itself urgent.
    if action == "notify" and s.in_dnd and not (s.urgent or s.direct_mention):
        action = "digest"
        why.append("held for the digest because it arrived during quiet hours")

    # Notification fatigue raises the bar for interrupting, but never
    # suppresses something urgent.
    if action == "notify" and s.load_high and not s.urgent:
        action = "digest"
        why.append("user is already above their normal notification volume")

    confidence = 0.86 if action == "notify" else 0.83 if action == "mute" else 0.80
    if mtype == "unknown":
        confidence = 0.78
    if s.direct_mention and s.urgent:
        confidence = 0.89

    return Decision(
        message_id=m.message_id,
        action=action,
        message_type=mtype,
        reason=_reason(why),
        confidence=confidence,
        evidence_message_ids=_evidence(ctx, action),
    )


def _evidence(ctx: MessageContext, action: str, limit: int = 2) -> list[str]:
    """Placeholder evidence selection. M4 replaces this with similarity plus
    outcome-informativeness ranking; for now, prefer same-conversation history
    whose recorded outcome is consistent with the action we chose."""
    want_opened = action == "notify"
    scored: list[tuple[int, str]] = []
    for h in ctx.history:
        hid = h.get("message_id", "")
        ev = ctx.events.get(hid, {})
        if not ev:
            continue
        same_conv = (
            (h.get("group_id") and h.get("group_id") == ctx.message.group_id)
            or (h.get("business_id") and h.get("business_id") == ctx.message.business_id)
            or (h.get("sender_user_id") and h.get("sender_user_id") == ctx.message.sender_user_id)
        )
        opened = str(ev.get("message_opened", "")) == "1"
        score = (2 if same_conv else 0) + (1 if opened == want_opened else 0)
        if score:
            scored.append((score, hid))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [hid for _, hid in scored[:limit]]


def personalize_all(contexts: list[MessageContext]) -> list[Decision]:
    return [personalize(c) for c in contexts]


def render_signals(ctx: MessageContext) -> str:
    """Render the personalization signals for an LLM prompt.

    This exists so that choosing an LLM provider cannot bypass M3. The signals
    are computed here and injected into every routing prompt, so the model
    reasons over the same structured evidence the rules use rather than
    re-deriving it from raw CSV rows.
    """
    s = signals_for(ctx)
    flags = [
        ("directly names the recipient", s.direct_mention),
        ("user has MUTED this group", s.group_muted),
        ("user is disengaged from this group", s.group_disengaged),
        ("mass-forwarded / chain content", s.is_chain or s.heavily_forwarded),
        ("time-sensitive", s.urgent and not s.defuses_urgency),
        ("sender says no immediate response needed", s.defuses_urgency),
        ("promotional", s.promo),
        ("user does NOT accept promotions from this sender", s.promo_unwanted),
        ("dormant relationship with this sender", s.relationship_stale),
        ("active relationship with this sender", s.relationship_active),
        ("arrived during the user's quiet hours", s.in_dnd),
        ("user already above their normal notification volume", s.load_high),
        ("no prior history with this sender", s.unknown_sender),
    ]
    on = [label for label, value in flags if value]
    lines = ["Personalization signals (precomputed, treat as reliable):"]
    lines += [f"  - {label}" for label in on] or ["  - (none)"]
    if s.group_dismissal_rate is not None:
        lines.append(f"  - dismisses {s.group_dismissal_rate:.0%} of this group's notifications")
    return "\n".join(lines)
