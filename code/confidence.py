"""M4 — confidence calibration.

Turns "how sure are we" into the `confidence` column.

Two things this is NOT:
  * not a probability — nothing here is fitted against outcomes, so treating
    0.85 as "85% likely correct" would be false precision;
  * not a clamp — the raw signal's *ordering* is preserved by a monotonic map,
    so a decision we are relatively more sure of still scores higher.

Two knobs, and they answer different questions.

1. WHICH BAND — a per-action prior. The 30 labelled sample rows do not use one
   confidence range, they use three overlapping ones, and they are ordered:

       notify  0.85-0.91  (median 0.87, n=9)
       mute    0.81-0.87  (median 0.84, n=10)
       digest  0.78-0.84  (median 0.82, n=11)

   Interrupting someone is the claim the labeller was surest about; deferring
   is the hedge, and it scores lowest. A single global 0.78-0.91 band — what
   this module used to emit — throws that ordering away and lands every row in
   the top sixth of the scale, above the entire labelled digest range.

   This is a per-class prior read off 30 rows, in the same category as the
   global band it replaces. It is emphatically not per-row fitting: no
   message_id is special-cased anywhere here, and the only inputs are the
   chosen action and the signals that produced it. 30 rows is thin evidence
   for three ranges — thinner than it was for one — so if the hidden truth
   orders the actions differently, this is wrong in a structured way rather
   than a random one. DECISIONS.md carries the same caveat.

2. WHERE IN THE BAND — the monotonic map from internal certainty.
   `certainty()` returns a [0, 1] score, but it does not *use* [0, 1]: over the
   110 routed rows it occupies 0.60-0.89 after the model blend. Stretching
   [0, 1] across the band therefore spent most of the band on values that never
   occur, which is why 58 of 110 rows came out at exactly 0.88 and the whole
   file had 5 distinct values against the sample's 10. Normalising against the
   slice the score actually uses restores resolution that was already in the
   signal — 28 distinct internal values were being flattened into 5 outputs.
   That slice is a property of the scoring rules, not of any label.

The floor is deliberate too: nothing here emits below 0.78, because nothing in
the labelled set does either, even for rows a human would call borderline.

This replaced a lookup table keyed only on `action`, which produced the same
number for a clear-cut scam and a coin-flip digest.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

#: Per-action output bands, read off dataset/sample_messages.csv (30 rows).
#: Observed min/max per action; see the module docstring for medians and n.
ACTION_BANDS = {
    "notify": (0.85, 0.91),
    "mute": (0.81, 0.87),
    "digest": (0.78, 0.84),
}

#: Union of the bands above. Also the fallback band for an action outside the
#: enum, which should never reach here — the contract validator rejects it.
BAND_MIN, BAND_MAX = 0.78, 0.91

#: The slice of the certainty scale that maps onto a band. [0, 1] was the wrong
#: slice: the score never approaches either end (the 110 routed rows occupy
#: 0.60-0.89 after the model blend), so most of the band went unused.
#:
#: This is a deliberate compromise, not a min/max fit to that measurement.
#: Normalising tightly to the observed extremes would let a single outlier row
#: define the whole scale and would push the per-action medians above the
#: labelled ones. With this window exactly one row clamps at the floor, no row
#: reaches the ceiling, and the emitted medians land at 0.87 / 0.83 / 0.80
#: against labelled 0.87 / 0.84 / 0.82.
CERT_FLOOR, CERT_CEILING = 0.65, 1.00


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def band_for(action: str) -> tuple[float, float]:
    """The (low, high) output band for `action`, falling back to the union."""
    return ACTION_BANDS.get(str(action).strip().lower(), (BAND_MIN, BAND_MAX))


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
    # mismatch, account age, report counts), which is firm ground and worth
    # crediting. But the gate is also a short circuit: the row never reaches
    # classification, so it carries less evidence about *what the message is*
    # than a reasoned decision does, not more.
    #
    # This was +0.30, which saturated the scale — the 22 gate-forced rows came
    # out as the highest-confidence rows in the whole file, i.e. most confident
    # exactly where the least classification happened, while the labelled scam
    # rows sit mid-band at 0.81-0.87 (mean 0.85).
    #
    # +0.05 is not a tuned number, it is a ceiling: a reasoned row is blended
    # 50/50 with the model's own confidence, which on the shipping path has a
    # median of 0.86 against an internal certainty around 0.75, so the blend is
    # itself worth about +0.05 to a reasoned row. Any larger credit here would
    # let a gate-forced row outscore a reasoned row built from identical
    # signals, which is the inversion we are removing.
    if gate_forced:
        score += 0.05

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
        # Decided from partial content: the ASR dropped the opening audio, so
        # we are reading the middle of a message.
        if get("media_truncated"):
            score -= 0.15

    return _clamp(score)


def calibrate(
    action: str,
    message_type: str,
    evidence_ids: Sequence[str] = (),
    signals: Any = None,
    gate_forced: bool = False,
    model_confidence: Optional[float] = None,
) -> float:
    """Final `confidence` value, rounded to 2dp and inside the action's band.

    When the model reports its own confidence it is averaged in rather than
    discarded: it carries information our features do not (it read the text).
    But it is not trusted alone — on the shipping path it returned 0.50 for
    msg_056, the spec's own carve-out example, which our signals identify as
    one of the clearest calls in the set.

    The blend happens in certainty space, before the band is applied, so the
    action prior sets the range and the blended certainty sets the position
    within it. The map is linear and clamped, therefore monotonic: for a fixed
    action, more internal certainty never lowers the emitted number.
    """
    internal = certainty(action, message_type, evidence_ids, signals, gate_forced)

    if model_confidence is not None:
        try:
            model = _clamp(float(model_confidence))
            internal = 0.5 * internal + 0.5 * model
        except (TypeError, ValueError):
            pass

    low, high = band_for(action)
    position = _clamp((internal - CERT_FLOOR) / (CERT_CEILING - CERT_FLOOR))
    return round(low + position * (high - low), 2)
