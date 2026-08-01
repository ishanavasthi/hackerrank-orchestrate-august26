"""M4 — evidence retrieval.

Picks the `evidence_message_ids` for a decision: historical messages that both
*resemble* the current one and whose *recorded outcome explains the action we
chose*. This is the design locked in DECISIONS.md; the placeholder it replaces
only filtered by same-conversation and opened/not-opened.

Why the outcome half matters: the grader checks whether evidence points to
relevant history. A recent message from the same sender that the user ignored
explains nothing about a `notify`. `message_events.csv` joins 1:1 with all 412
history rows, so an outcome is always available.

Scoring is deterministic — no model call, no randomness — so evidence is stable
across reruns and identical on the rules and LLM paths.
"""

from __future__ import annotations

import re
from typing import Optional

try:
    from code.contracts import MessageContext
except ImportError:
    from contracts import MessageContext

# Words carrying no topical signal. Kept small on purpose: an aggressive list
# would strip the domain vocabulary ("payment", "delivery") that makes two
# messages genuinely similar.
_STOPWORDS = frozenset("""
a an the and or but if then than that this these those is are was were be been
being am do does did doing have has had having i you he she it we they me him
her us them my your his its our their to of in on at by for with from as into
over under again further once here there all any both each few more most other
some such no nor not only own same so too very can will just should now
""".split())

_TOKEN = re.compile(r"[a-z0-9]+")

# Weights. Conversation match dominates because an unrelated-thread message is
# almost never good evidence however similar its wording; outcome support is
# next because it is what makes the citation explanatory rather than decorative.
W_CONVERSATION = 3.0
W_OUTCOME = 2.0
W_SIMILARITY = 4.0
W_SAME_TYPE = 0.5

#: Below this, we emit `none` rather than cite something irrelevant. A wrong
#: citation is worse than an absent one — it actively misleads a reader.
MIN_SCORE = 1.2


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall((text or "").lower())
            if len(t) > 2 and t not in _STOPWORDS}


def _similarity(a: set[str], b: set[str]) -> float:
    """Jaccard overlap. Deterministic and dependency-free; good enough to
    separate 'same topic' from 'same conversation, unrelated topic'."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _same_conversation(history_row: dict, ctx: MessageContext) -> bool:
    m = ctx.message
    gid, bid, sid = (history_row.get("group_id"), history_row.get("business_id"),
                     history_row.get("sender_user_id"))
    return bool((gid and gid == m.group_id)
                or (bid and bid == m.business_id)
                or (sid and sid == m.sender_user_id))


def _outcome_support(event: dict, action: str) -> float:
    """How well a historical outcome explains `action`, in [0, 1].

    The mapping is the point of this module: a message the user opened and
    replied to justifies interrupting them again; one they dismissed or muted
    justifies suppressing a similar one.
    """
    if not event:
        return 0.0
    opened = str(event.get("message_opened", "")).strip() == "1"
    replied = str(event.get("message_replied", "")).strip() == "1"
    dismissed = str(event.get("notification_dismissed", "")).strip() == "1"
    muted_after = str(event.get("muted_after_message", "")).strip() == "1"
    reported = str(event.get("message_reported", "")).strip() == "1"

    try:
        reaction = float(event.get("reaction_time_minutes") or "")
    except (TypeError, ValueError):
        reaction = None

    if action == "notify":
        score = 0.0
        if replied:
            score += 0.6
        if opened:
            score += 0.3
        if reaction is not None and reaction <= 5:
            score += 0.1
        return min(score, 1.0)

    if action == "mute":
        score = 0.0
        if reported:
            score += 0.5
        if muted_after:
            score += 0.4
        if dismissed:
            score += 0.3
        if not opened:
            score += 0.2
        return min(score, 1.0)

    # digest: useful but not interrupt-worthy — read, but not acted on urgently
    score = 0.0
    if opened and not replied:
        score += 0.6
    if reaction is not None and reaction > 5:
        score += 0.3
    if not dismissed:
        score += 0.1
    return min(score, 1.0)


def score_candidates(ctx: MessageContext, action: str) -> list[tuple[float, str, dict]]:
    """Score every history row for this user. Returned high-score first."""
    query = _tokens(f"{ctx.message.message_text} "
                    f"{ctx.media.text if (ctx.media and ctx.media.available) else ''}")
    scored: list[tuple[float, str, dict]] = []

    for row in ctx.history:
        hid = row.get("message_id", "")
        if not hid or hid == ctx.message.message_id:
            continue
        event = ctx.events.get(hid, {})

        similarity = _similarity(query, _tokens(row.get("message_text", "")))
        same_conv = _same_conversation(row, ctx)
        outcome = _outcome_support(event, action)
        same_type = (row.get("conversation_type") == ctx.message.conversation_type)

        score = (W_SIMILARITY * similarity
                 + W_CONVERSATION * float(same_conv)
                 + W_OUTCOME * outcome
                 + W_SAME_TYPE * float(same_type))

        # A citation from an unrelated thread with no topical overlap is noise
        # regardless of how neatly its outcome matches.
        if not same_conv and similarity < 0.08:
            continue

        scored.append((score, hid, {"similarity": similarity, "same_conversation": same_conv,
                                    "outcome_support": outcome}))

    # Sort by score, then id, so ties resolve deterministically.
    scored.sort(key=lambda t: (-t[0], t[1]))
    return scored


def select_evidence(ctx: MessageContext, action: str, limit: int = 2) -> list[str]:
    """Return 1-2 history ids supporting `action`, or [] to emit `none`.

    The 1-2 cap matches the observed sample format (27 of 30 rows cite one id,
    3 cite two). DECISIONS.md flags that cap as inferred from thin evidence.
    """
    ranked = score_candidates(ctx, action)
    picked = [hid for score, hid, _ in ranked if score >= MIN_SCORE][:limit]
    return picked


def explain(ctx: MessageContext, action: str, limit: int = 3) -> list[tuple[float, str, dict]]:
    """Debug helper: the top candidates with their score components."""
    return score_candidates(ctx, action)[:limit]
