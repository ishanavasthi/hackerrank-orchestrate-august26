"""M4a — user-business affinity.

Asks one question about a business message that has already cleared the safety
gate and already has a decision: does THIS user have an *open obligation* with
THIS sender — something booked, on its way, due, or owed — and if so, is
deferring it to the digest the wrong answer?

It reads exactly one column, `why_user_knows_account` from
`user_business_history.csv`, tokenised on underscores, plus a short list of
multi-token phrases matched as substrings. It can only ever raise attention,
never lower it, and on an unrecognised relationship it does nothing at all.

WHY NOT ENGAGEMENT. The obvious rule is "this user opens far more of this
sender's messages than they dismiss, so interrupt them". It is wrong, and the
labelled samples falsify it directly — four heavily-engaged business rows split
two-two:

    sample_msg_004  recent_grocery_delivery      5 opened / 1 dismissed  notify
    sample_msg_005  upcoming_clinic_appointment  6 opened / 1 dismissed  notify
    sample_msg_007  travel_package_interest      6 opened / 1 dismissed  DIGEST
    sample_msg_011  recent_movie_feedback        6 opened / 0 dismissed  DIGEST

Engagement carries no discriminating information across those four. What
separates them is the KIND of relationship. sample_msg_007 is a browsing
interest and sample_msg_011 is a feedback tie: the user reads them happily and
still does not need to be interrupted by them. A commitment is different,
because something is already in motion that the user is party to.

That cut was measured, not assumed. Five engagement gates (`o >= d`, `o > d`,
`o >= 2d`, `o >= 2*max(d, 1)`, and `o >= 2d+1 or (o >= d and r >= 1)`) were each
added on top of the predicate below and every one produced an identical result
on all 110 rows: the predicate fires on 7 rows in the whole dataset (msg_003,
msg_004, msg_025, msg_049, msg_050, msg_075, msg_086) and every one of them is
already heavily engaged, so there is nothing for a gate to reject. A term that
decides nothing does not belong in the code.

`activity_count_180d`, `last_activity_at` and `last_reply_at` are unread for the
same reason. `last_reply_at` is blank in exactly the 42 rows where
`messages_replied_30d == 0` and equals `last_activity_at` in all 64 rows where
it is present, so it restates a column we already have; and
`activity_count_180d <= 4` fits the four labelled rows above with an *inverted*
causal sign and is falsified by msg_050 (which has 5).

DIRECTION. This stage may only raise attention. `_UPGRADE` is the complete
transition table, `mute` is deliberately absent from it, and
`_assert_upgrade_only` refuses a downhill entry at import time. A `mute`
arriving here is returned untouched in BOTH columns — the early return in
`apply_affinity` covers the `message_type` leg as well, because relabelling a
suppressed row is overturning the suppression too. Suppression belongs to the
safety gate (M2) and to personalization (M3); a preference signal must never be
able to argue a mute back into the tray.
"""

from __future__ import annotations

import re

try:
    from contracts import Decision, MessageContext
except ImportError:  # running as part of the `code` package
    from code.contracts import Decision, MessageContext


# ─── The relationship vocabulary ────────────────────────────────────────────

#: An OPEN OBLIGATION the user is party to: something is booked, on its way,
#: due, or owed. Chosen a priori from what the words MEAN — the four semantic
#: families below — not fitted to the 90 observed `why_user_knows_account`
#: values. Every member of every family is listed whether or not this dataset
#: exercises it; pruning to the members that happen to fire is exactly the
#: fit-to-the-test-set this rule exists to avoid.
#:
#: Coverage in this dataset (measured, not asserted): 18 of these 48 tokens
#: occur somewhere in the 106 rows of user_business_history.csv; 11 occur in the
#: 18 relationships actually reachable from messages.csv; and 10 of those take
#: part in a fire — appointment, booked, booking, confirmed, delivery, expected,
#: pickup, prescription, refill, upcoming ("payment" occurs only inside
#: `business_payment_stack_interest`, which the veto set correctly rejects).
#: The other 30 are unexercised here and are kept deliberately.
_COMMITMENT: frozenset[str] = frozenset({
    # scheduled / booked: a time is held for this user
    "appointment", "appointments", "booking", "bookings", "booked",
    "reservation", "reservations", "reserved", "scheduled", "upcoming",
    "confirmed",
    # in motion: something is already on its way to or from this user
    "delivery", "deliveries", "order", "orders", "shipment", "shipped",
    "dispatched", "arriving", "transit", "pickup", "pickups", "expected",
    "awaiting", "pending",
    # due or owed: money or an action is outstanding
    "payment", "payments", "bill", "bills", "billing", "invoice", "invoices",
    "due", "dues", "overdue", "outstanding", "fee", "fees", "renewal",
    "renewals", "installment", "repayment", "emi",
    # health / claims: an obligation already filed and running
    "prescription", "refill", "refills", "claim", "claims",
})

#: A marketing tie, a browse tie, an opinion tie, a dormant tie, or a CLOSED /
#: NEGATED commitment. VETOES the set above, so one commitment-ish token can
#: never drag a marketing relationship up: `business_payment_stack_interest`
#: carries "payment" and `old_delivery_order` carries both "delivery" and
#: "order", and all of them must lose. `old` / `abandoned` / `ignored` are the
#: dormancy convention personalize.py already uses for `relationship_stale`.
#:
#: The CLOSED/NEGATED family is the one the first version was missing entirely,
#: and it is the expensive gap: `cancelled_appointment`, `refunded_order`,
#: `expired_booking`, `closed_order_survey` and `declined_loan_payment_offer`
#: all carry a perfectly good admit token and all were upgraded to notify.
#:
#: The asymmetry is deliberate and one-directional. A missing VETO token emits a
#: false interrupt — the user is woken by an advert. A missing ADMIT token emits
#: a missed interrupt — a real delivery waits in the digest, which the user still
#: sees. The first error is strictly worse, so this set is written wide and
#: `_COMMITMENT` is written narrow, and every genuinely ambiguous word (e.g.
#: "review", which is a rating request far more often than a claim under review)
#: is resolved into the veto set.
_NOT_COMMITMENT: frozenset[str] = frozenset({
    # marketing push: the sender wants attention, the user owes nothing
    "promotion", "promotions", "promotional", "promo", "promos",
    "offer", "offers", "deal", "deals", "discount", "discounts",
    "sale", "sales", "coupon", "coupons", "voucher", "vouchers",
    "cashback", "newsletter", "newsletters", "marketing", "ads", "advert",
    "advertising", "campaign", "blast", "loyalty", "rewards", "giveaway",
    "sponsored", "spam", "junk", "bulk",
    # a broadcast list or a recurring subscription is not an obligation
    "list", "lists", "mailing", "subscription", "subscriptions",
    "subscribe", "subscribed", "signup",
    # browse / intent: looked at, never committed to
    "interest", "interests", "interested", "search", "searches", "searched",
    "browse", "browsing", "browsed", "viewed", "wishlist", "watchlist",
    "saved", "listing", "listings", "catalog", "recommendation",
    "recommendations", "suggested", "suggestions", "explore",
    # opinion: the sender wants the user's view, which can always wait
    "feedback", "survey", "surveys", "review", "reviews", "rating",
    "ratings", "poll", "testimonial", "questionnaire",
    # dormant relationship
    "old", "older", "former", "previous", "past", "stale", "dormant",
    "inactive", "lapsed", "abandoned", "ignored", "unused", "archived",
    # CLOSED or NEGATED commitment: it existed and no longer does
    "cancelled", "canceled", "cancellation", "closed", "complete",
    "completed", "finished", "fulfilled", "delivered", "collected",
    "returned", "refunded", "declined", "rejected", "denied", "failed",
    "void", "voided", "withdrawn", "expired", "settled", "terminated",
    "ended", "unsubscribe", "unsubscribed", "blocked", "muted", "unwanted",
    "disabled",
})

#: Multi-token phrases matched as SUBSTRINGS of the normalised value, because
#: they cannot be expressed as single tokens. The opt-out family is the whole
#: reason this exists: the bare token "out" would veto `parcel_out_for_delivery`
#: and `order_out_for_delivery` — "out for delivery" being the standard courier
#: phrase — so the rule would silently do nothing for precisely the open
#: deliveries it is written to rescue. personalize.py already uses the correct
#: narrow test (`"opted_out" in why`); this matches it. Sorted tuple, not a set,
#: so iteration order cannot vary between runs.
_NOT_COMMITMENT_PHRASES: tuple[str, ...] = (
    "opt_out", "opted_out", "opting_out",
)


# ─── Direction guard ────────────────────────────────────────────────────────

#: Attention order. Exists only so the transition table can be proved uphill.
_ATTENTION: dict[str, int] = {"mute": 0, "digest": 1, "notify": 2}

#: The COMPLETE set of transitions this stage may make, keyed by the incoming
#: action. Anything absent is left exactly as the upstream stage decided it.
#:
#: `mute` is absent and must stay absent. Gate-forced mutes never reach here at
#: all — main.py assembles those rows itself and never calls personalize() or
#: route() for them — but a low-value mute from M3 does reach here, and this
#: stage has no standing to overturn it either.
_UPGRADE: dict[str, str] = {"digest": "notify"}

#: Types this stage refuses to upgrade, whatever the relationship says.
#:
#: `promotion` is here because of msg_075 (`ride_booked_today`, "Your pickup or
#: route status has changed"). On the rules path that row is typed `promotion`
#: by classify_type's business fallthrough — a DEFAULT, not a positive call:
#: 10 of the 21 promotion-typed rows have `Signals.promo` False. Upgrading it to
#: `notify` handed `personalize.enforce_promotion_policy` a notify/promotion to
#: demote, and the composition landed the row at `mute` — BELOW the `digest` it
#: arrived with, breaking the upgrade-only property this module advertises two
#: paragraphs up and `_assert_upgrade_only` enforces on the table alone. Neither
#: stage demotes by itself; only the pair does, so the guard belongs here, where
#: the upgrade that starts the sequence is made. msg_049 (`recent_return_pickup`)
#: is the same shape.
#:
#: `spam` and `scam` are here for the mirror-image reason: a risk label is
#: safety.py's verdict, and a preference signal has no standing to argue with it.
_NOT_UPGRADABLE: frozenset[str] = frozenset({"promotion", "spam", "scam"})

#: REPLACES the reason when the action is upgraded. The upstream reason was
#: written to justify the decision this stage just overturned, so appending a
#: clause to it shipped rows that said `notify` and read "...they find them
#: relevant but not urgent" (msg_003) or "...does not require immediate
#: interruption" (msg_004). `reason` is a scored column; it has to agree with
#: the action beside it.
#:
#: A constant is also the only version with a knowable length. The append ran
#: through `[:500]`, which on a long model reason would have cut the appended
#: clause — the override's only trace — mid-word. 138 characters, inside the
#: 165-char budget safety.py holds its own composed reasons to
#: (`safety._REASON_LIMIT`). That module's `_fit()` is deliberately NOT reused:
#: it assembles a four-part sentence by shedding parts until it fits, and a
#: fixed string has nothing to shed.
UPGRADE_REASON = (
    "The user has an open commitment with this business (a booking, delivery "
    "or order already in motion), so this update should reach them now."
)


def _assert_upgrade_only(table: dict[str, str]) -> None:
    """Fail at import if the transition table would ever demote a decision.

    A plain `assert` would vanish under `python -O`, and this is the guard that
    keeps a future edit from quietly turning a preference signal into a
    suppression signal, so it raises unconditionally.
    """
    for before, after in sorted(table.items()):
        if _ATTENTION[after] <= _ATTENTION[before]:
            raise AssertionError(
                f"affinity may only raise attention: {before} -> {after} does not"
            )


_assert_upgrade_only(_UPGRADE)


# ─── The predicate ──────────────────────────────────────────────────────────

def open_commitment(ctx: MessageContext) -> bool:
    """True when the user has an outstanding obligation with this sender.

    One boolean, admit-minus-veto, veto wins. That is the whole classifier —
    there is no intent taxonomy and no precedence chain, because a two-outcome
    question does not need either.

    Silence is the default and the fallback is total: an UNSEEN value carrying
    no admit token returns False, and so do an empty `business_history`, a blank
    value, a None, a missing key, and a value that tokenises to nothing. An
    unrecognised relationship therefore leaves the upstream decision exactly as
    it was — the rule never guesses.

    Values are normalised before tokenising, so a held-out `opted-out` or
    `opted out` is read the same as `opted_out`.

    BUSINESS MESSAGES ONLY. `business_history` is joined on
    `(user_id, business_id)` with no `conversation_type` filter (data.py:167),
    so any row carrying a `business_id` picks up the relationship whatever its
    type — a personal or group message with a populated `business_id` would be
    upgraded on a business relationship it has nothing to do with. No such row
    exists in `messages.csv` today; this guard is what keeps that a fact about
    the data instead of an assumption this rule leans on.
    """
    if ctx.message.conversation_type != "business":
        return False

    raw = str(ctx.business_history.get("why_user_knows_account", "") or "").lower()
    why = re.sub(r"[^a-z0-9]+", "_", raw)
    if any(phrase in why for phrase in _NOT_COMMITMENT_PHRASES):
        return False
    tokens = set(why.split("_"))
    return bool(tokens & _COMMITMENT) and not (tokens & _NOT_COMMITMENT)


def apply_affinity(
    ctx: MessageContext, decision: Decision, content_type: str
) -> Decision:
    """Apply the affinity override in place and return the same Decision.

    Called from BOTH routing paths — personalize() and router._apply_m4 — so the
    rules engine and the model cannot disagree about a relationship.

    ORDERING IS LOAD-BEARING. This must run *before* evidence selection and
    confidence calibration, because both are keyed on `decision.action`. Applied
    after them, an upgraded row would carry digest-ranked evidence and a digest
    confidence while reporting `notify` — internally inconsistent, and nothing
    in validate.py would catch it.

    `content_type` is the caller's `personalize.classify_type()` verdict, passed
    in rather than imported so this module stays free of the module that imports
    it. It grounds the retype in the message TEXT instead of in the relationship
    label, which is the point: msg_050 is `prescription_refill` and reads like an
    appointment reminder, but msg_086 is `confirmed_travel_booking` with a voice
    note we should not retype on the strength of the label alone.
    """
    # DIRECTION GUARD — covers BOTH legs. `_UPGRADE` proves only that the ACTION
    # leg moves uphill; this is what proves the type leg does too. A `mute` is a
    # suppression an earlier stage committed to, and relabelling the
    # `message_type` of a suppressed row overturns that stage's judgement just as
    # much as raising the action would. Without this, msg_004's context handed in
    # as `mute`/`business_update` came back `mute`/`event` — the module docstring
    # advertising a property the code did not enforce.
    #
    # It guards the INCOMING action, not the outgoing one, so msg_050's
    # notify/business_update -> notify/event retype survives.
    #
    # `_NOT_UPGRADABLE` is the second half of the same guard and exists for
    # msg_075: an upgrade out of a promotion-typed row is one the promotion
    # invariant then has to undo, and the pair lands the row lower than it
    # started. See the constant.
    if (decision.action == "mute"
            or decision.message_type in _NOT_UPGRADABLE
            or not open_commitment(ctx)):
        return decision

    upgraded = _UPGRADE.get(decision.action)
    if upgraded is not None:
        decision.action = upgraded
        decision.reason = UPGRADE_REASON

    # The type leg corrects the generic business default and only that. Confining
    # it to `business_update` stops it overwriting a type the upstream stage
    # actually committed to: msg_075 (`ride_booked_today`) fires this predicate
    # and is typed `urgent` by the nvidia model but `promotion` by classify_type
    # on the rules path — neither is `business_update`, so neither is touched
    # here (and the `promotion` one no longer reaches this line at all).
    if decision.message_type == "business_update" and content_type == "event":
        decision.message_type = "event"

    return decision
