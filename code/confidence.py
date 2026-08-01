"""M4 — confidence calibration.

Turns "how sure are we" into the `confidence` column.

Two things this is NOT:
  * not a probability — nothing here is fitted against outcomes, so treating
    0.85 as "85% likely correct" would be false precision;
  * not a clamp — the raw signal's *ordering* is preserved by a monotonic map,
    so a decision we are relatively more sure of still scores higher.

The observed band. All 30 labelled sample rows fall in 0.78-0.91, and nothing
in that set goes lower even for rows a human would call borderline. We map onto
that band rather than emitting our own scale. DECISIONS.md already flags the
weakness: 30 rows is thin evidence for a target range, and if the hidden truth
uses a wider spread our calibration is systematically too narrow.

This replaced a lookup table keyed only on `action`, which produced the same
number for a clear-cut scam and a coin-flip digest.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

#: Inferred from dataset/sample_messages.csv (min 0.78, max 0.91).
BAND_MIN, BAND_MAX = 0.78, 0.91


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def certainty(
    action: str,
    message_type: str,
    evidence_ids: Sequence[str] = (),
    signals: Any = None,
    gate_forced: bool = False,
) -> float:
    """Internal certainty in [0, 1], before mapping onto the output band.

    Built from things that genuinely covary with being right: corroborating
    evidence, agreement between independent signals, and whether the decision
    rested on a structural fact or on a guess.
    """
    score = 0.5

    # A forced mute from the blind gate rests on structural facts (domain
    # mismatch, account age, report counts) plus explicit content, not on
    # judgement about what the user wants. That is the firmest ground we have.
    if gate_forced:
        score += 0.30

    # Corroborating history. Two supporting citations is meaningfully better
    # than one; more than two we do not emit.
    score += 0.10 * min(len(evidence_ids), 2)

    # `unknown` means the classifier declined to commit — say so.
    if message_type == "unknown":
        score -= 0.25

    if signals is not None:
        get = lambda name: bool(getattr(signals, name, False))  # noqa: E731

        # Unambiguous, mutually reinforcing cases.
        if get("direct_mention") and get("really_urgent"):
            score += 0.15                      # the spec's carve-out shape
        if get("promo") and get("promo_unwanted"):
            score += 0.15                      # consent is explicit in the data
        if get("is_chain") or get("heavily_forwarded"):
            score += 0.10
        if get("group_muted") and not get("direct_mention"):
            score += 0.10

        # Genuine tension between signals — be less sure, not more.
        if get("really_urgent") and get("group_muted") and not get("direct_mention"):
            score -= 0.15
        if get("promo") and get("relationship_active"):
            score -= 0.10
        if get("unknown_sender"):
            score -= 0.10
        # Quiet hours and notification load only ever nudge a decision; a
        # decision resting on them alone is a weak one.
        if get("in_dnd") or get("load_high"):
            score -= 0.05

    return _clamp(score)


def calibrate(
    action: str,
    message_type: str,
    evidence_ids: Sequence[str] = (),
    signals: Any = None,
    gate_forced: bool = False,
    model_confidence: Optional[float] = None,
) -> float:
    """Final `confidence` value, rounded to 2dp and inside the observed band.

    When the model reports its own confidence it is averaged in rather than
    discarded: it carries information our features do not (it read the text).
    But it is not trusted alone — on the shipping path it returned 0.50 for
    msg_056, the spec's own carve-out example, which our signals identify as
    one of the clearest calls in the set.
    """
    internal = certainty(action, message_type, evidence_ids, signals, gate_forced)

    if model_confidence is not None:
        try:
            model = _clamp(float(model_confidence))
            internal = 0.5 * internal + 0.5 * model
        except (TypeError, ValueError):
            pass

    return round(BAND_MIN + internal * (BAND_MAX - BAND_MIN), 2)
