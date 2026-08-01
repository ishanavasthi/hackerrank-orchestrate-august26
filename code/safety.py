"""M2 — the blind safety gate.

Runs BEFORE personalization and can force `mute` with `scam`/`spam` on its own.

The defining property is what it is NOT allowed to see. Per DECISIONS.md, the
spec requires clear risk to be muted "regardless of the user's usual
engagement", and ordering alone does not achieve that: a stage that can see
"this user replies to this sender constantly" can rationalise its way out of a
correct flag. Withholding the context is what enforces the rule.

So blindness here is structural, not aspirational:
  * SafetyContext enumerates every field the gate may see. There is no
    passthrough and no **kwargs.
  * build_safety_context() reads only message/media/business/group. It never
    touches ctx.user, ctx.membership, ctx.business_history, ctx.history,
    ctx.events, or ctx.notification_load.
  * assert_blind() re-checks the rendered prompt for engagement field names,
    so a future edit that reintroduces them fails loudly.

Scope discipline: this gate fires on *risk* (deception, credential theft,
impersonation, coercion). It does NOT fire on merely low-value or annoying
promotional content — muting that is a personalization judgement and belongs
to M3. A gate that mutes boring marketing is a gate that will eventually mute
something a user wanted.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
from dataclasses import asdict, dataclass, field
from typing import Optional

try:
    from code.contracts import MessageContext
except ImportError:
    from contracts import MessageContext

REPO = pathlib.Path(__file__).resolve().parent.parent
CACHE_DIR = REPO / "code" / "cache" / "safety"

# Field names that must never reach the gate. Used by assert_blind() as a
# tripwire over the rendered prompt.
FORBIDDEN_ENGAGEMENT_FIELDS: frozenset[str] = frozenset({
    "messages_opened_30d", "messages_replied_30d", "notifications_dismissed_30d",
    "messages_reported_30d", "messages_read_30d", "replies_sent_30d",
    "group_muted_by_user", "why_user_knows_account", "allows_promotions",
    "promotions_opted_out_at", "activity_count_180d", "messages_dismissed_30d",
    "last_reply_at", "last_activity_at", "message_opened", "message_replied",
    "reaction_time_minutes", "notification_dismissed", "muted_after_message",
    "message_reported", "notifications_sent",
})


# ─── The blindness boundary ─────────────────────────────────────────────────

@dataclass(frozen=True)
class SafetyContext:
    """Every field the safety gate is permitted to see. Nothing else exists."""
    message_id: str
    conversation_type: str
    created_at: str
    message_text: str
    media_type: str
    media_text: str
    forwarded_count: int

    # Structural sender facts. `user_reports_30d` is a GLOBAL report count
    # against the sender, not this user's behaviour, so it is admissible.
    has_business_record: bool = False
    display_name: str = ""
    brand_name: str = ""
    category: str = ""
    verified: Optional[bool] = None
    official_domain: str = ""
    domain_used_by_sender: str = ""
    account_age_days: Optional[int] = None
    domain_used_by_sender_age_days: Optional[int] = None
    user_reports_30d: Optional[int] = None
    messages_sent_30d: Optional[int] = None

    # Structural group facts (shape of the room, not the user's behaviour in it)
    group_type: str = ""
    group_member_count: Optional[int] = None


def _int(value, default=None) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def build_safety_context(ctx: MessageContext) -> SafetyContext:
    """Project a full MessageContext down to what the gate may see.

    This function is the blindness boundary. Do not add reads of ctx.user,
    ctx.membership, ctx.business_history, ctx.history, ctx.events, or
    ctx.notification_load — those are exactly the fields the gate must not
    weigh.
    """
    m = ctx.message
    biz = ctx.business or {}
    grp = ctx.group or {}
    media_text = ctx.media.text if (ctx.media and ctx.media.available) else ""

    verified = None
    if biz.get("verified") not in (None, ""):
        verified = str(biz["verified"]).strip() in {"1", "true", "True", "yes"}

    return SafetyContext(
        message_id=m.message_id,
        conversation_type=m.conversation_type,
        created_at=m.created_at,
        message_text=m.message_text or "",
        media_type=m.media_type or "",
        media_text=media_text,
        forwarded_count=_int(m.forwarded_count, 0) or 0,
        has_business_record=bool(biz),
        display_name=biz.get("display_name", "") or "",
        brand_name=biz.get("brand_name", "") or "",
        category=biz.get("category", "") or "",
        verified=verified,
        official_domain=(biz.get("official_domain", "") or "").strip(),
        domain_used_by_sender=(biz.get("domain_used_by_sender", "") or "").strip(),
        account_age_days=_int(biz.get("account_age_days")),
        domain_used_by_sender_age_days=_int(biz.get("domain_used_by_sender_age_days")),
        user_reports_30d=_int(biz.get("user_reports_30d")),
        messages_sent_30d=_int(biz.get("messages_sent_30d")),
        group_type=grp.get("group_type", "") or "",
        group_member_count=_int(grp.get("member_count")),
    )


# ─── Structural risk ────────────────────────────────────────────────────────

@dataclass
class RiskFeatures:
    domain_mismatch: bool = False
    impersonation: bool = False
    unverified_sender: bool = False
    young_account: bool = False
    young_domain: bool = False
    heavily_reported: bool = False
    heavily_forwarded: bool = False
    notes: list[str] = field(default_factory=list)


# A mismatch alone is NOT impersonation. Two legitimate patterns in this
# dataset would be falsely flagged by a naive comparison:
#   * verified, long-established senders using a link shortener
#     (Thrillophilia -> link.wame.pro, Polaris -> weurl.co)
#   * senders with no registered official_domain at all
#     (Green Cross Pharmacy, official_domain empty)
# Impersonation therefore requires a mismatch PLUS a corroborating signal.
YOUNG_ACCOUNT_DAYS = 180
YOUNG_DOMAIN_DAYS = 60
HEAVY_REPORTS = 15
HEAVY_FORWARDS = 5


def structural_risk(s: SafetyContext) -> RiskFeatures:
    f = RiskFeatures()

    if s.has_business_record:
        f.unverified_sender = s.verified is False
        f.young_account = s.account_age_days is not None and s.account_age_days < YOUNG_ACCOUNT_DAYS
        f.young_domain = (s.domain_used_by_sender_age_days is not None
                          and s.domain_used_by_sender_age_days < YOUNG_DOMAIN_DAYS)
        f.heavily_reported = s.user_reports_30d is not None and s.user_reports_30d >= HEAVY_REPORTS

        # A mismatch can only be claimed when an official domain exists to
        # mismatch against. Empty official_domain means no claim is possible.
        if s.official_domain and s.domain_used_by_sender:
            f.domain_mismatch = s.official_domain != s.domain_used_by_sender

        if f.domain_mismatch and (f.unverified_sender or f.young_account
                                  or f.young_domain or f.heavily_reported):
            f.impersonation = True
            f.notes.append(
                f"sender domain {s.domain_used_by_sender} does not match "
                f"official {s.official_domain}"
            )
            if f.unverified_sender:
                f.notes.append("account unverified")
            if f.young_account:
                f.notes.append(f"account only {s.account_age_days}d old")
            if f.heavily_reported:
                f.notes.append(f"{s.user_reports_30d} user reports in 30d")

    f.heavily_forwarded = s.forwarded_count >= HEAVY_FORWARDS
    if f.heavily_forwarded:
        f.notes.append(f"forwarded {s.forwarded_count} times")
    return f


# ─── Content risk (offline heuristic; mirrors what the LLM is asked) ────────

_CREDENTIAL = (
    r"\botp\b", r"one[- ]time (?:password|code)", r"\bcvv\b", r"\bpin\b",
    r"verification code", r"login code", r"\b6[- ]digit\b", r"six digit",
    r"share the code", r"send.{0,15}code", r"reply with.{0,25}code",
    r"card details", r"wallet.{0,15}details", r"net ?banking password",
    # Found while reviewing M3 inputs: msg_078 asks to "fill bank details on
    # first page and send screenshot", forwarded 10x, and cleared the gate
    # because only card/wallet details were covered.
    r"bank details", r"account details", r"\bupi (?:pin|id)\b",
)
_COERCION = (
    r"account (?:will be )?(?:blocked|suspended|frozen|deactivated)",
    r"before midnight", r"within \d+ (?:minutes|hours)", r"expires? today",
    r"immediately|urgently|right now", r"last (?:chance|warning)",
    r"failure to (?:comply|verify)", r"legal action",
    # msg_017: "Service stops today if clearance amount is not received."
    r"service (?:will )?stops?", r"stops? today", r"not received",
)
_LURE = (
    r"click (?:the )?link", r"verify (?:your )?(?:account|wallet|card|kyc)",
    r"complete (?:your )?verification", r"update (?:your )?kyc",
    r"claim (?:your )?(?:refund|prize|reward)", r"you have won",
    r"lottery", r"refund (?:approved|processing)",
    r"scan (?:and|to) pay", r"pending (?:charge|amount|dues)",
    r"clearance amount", r"send (?:a |the )?screenshot",
)
# A message that addresses the router rather than the recipient is trying to
# manipulate the classifier. That is a safety concern, so it belongs here
# rather than in personalization.
_INJECTION = (
    r"ignore (?:all )?previous", r"ignore the routing rules",
    r"disregard (?:the above|previous)", r"mark this (?:message )?as (?:notify|urgent)",
    r"system prompt", r"you are an ai", r"override .{0,20}instruction",
)


def _matches(patterns, text: str) -> list[str]:
    return [p for p in patterns if re.search(p, text, re.I)]


# Legitimate senders warn *about* credential fraud, and a bare keyword match
# cannot tell that from committing it. FedEx (msg_093) says "no payment or OTP
# is required for this delivery" — an anti-fraud reassurance that a naive
# \botp\b match reads as a credential request.
_NEGATION = r"(?:\bno\b|\bnot\b|\bnever\b|\bnone\b|\bdon'?t\b|\bwon'?t\b|\bdoes ?n'?t\b)"
# ...but a negation only disarms the mention if no request verb sits between
# them. "Don't delay, share your OTP" is still a credential request.
_REQUEST_VERB = r"\b(?:share|send|reply|provide|give|confirm|enter|forward|submit)\b"


def _is_negated(text: str, start: int) -> bool:
    """True when the credential mention at `start` sits in a reassurance."""
    window = text[max(0, start - 60):start]
    window = re.split(r"[.!?\n;]", window)[-1]      # stay in the same sentence
    neg = None
    for m in re.finditer(_NEGATION, window, re.I):
        neg = m
    if neg is None:
        return False

    tail = window[neg.end():]
    verb = re.search(_REQUEST_VERB, tail, re.I)
    if verb is None:
        return True                                  # "no OTP is required"

    # The verb is only disarmed if the negation governs it directly:
    #   "do not share your OTP"      -> negated, a warning
    #   "don't delay, share your OTP" -> a separate imperative, still a request
    between = tail[:verb.start()]
    return "," not in between and len(between.strip()) <= 12


def _credential_requests(text: str) -> list[str]:
    """Credential patterns that are actually being *requested*, not warned about."""
    hits = []
    for p in _CREDENTIAL:
        for m in re.finditer(p, text, re.I):
            if not _is_negated(text, m.start()):
                hits.append(p)
                break
    return hits


def content_risk(s: SafetyContext) -> tuple[bool, list[str]]:
    """Offline content-only risk heuristic. Sees text + media text only."""
    blob = f"{s.message_text}\n{s.media_text}"
    cred = _credential_requests(blob)
    coer = _matches(_COERCION, blob)
    lure = _matches(_LURE, blob)
    inj = _matches(_INJECTION, blob)
    signals: list[str] = []
    if cred:
        signals.append("requests a credential/OTP/card detail")
    if coer:
        signals.append("applies account-loss or deadline pressure")
    if lure:
        signals.append("pushes a verification/claim action")
    if inj:
        signals.append("addresses the router rather than the recipient")

    # Asking for a credential is on its own disqualifying: no legitimate
    # sender needs an OTP relayed back to them. Coercion or a lure alone is
    # ordinary marketing urgency; together they are a phishing shape.
    risky = bool(cred) or bool(inj) or (bool(coer) and bool(lure))
    return risky, signals


# ─── Verdict ────────────────────────────────────────────────────────────────

@dataclass
class SafetyVerdict:
    message_id: str
    force_mute: bool
    message_type: str = ""          # scam | spam when force_mute
    reason: str = ""
    confidence: float = 0.0
    signals: list[str] = field(default_factory=list)


def safety_verdict(ctx: MessageContext, provider: str = "stub") -> SafetyVerdict:
    s = build_safety_context(ctx)
    struct = structural_risk(s)
    content_risky, content_signals = content_risk(s)

    if provider != "stub":
        llm = _llm_safety(s, provider)
        if llm is not None:
            content_risky = llm.get("risky", content_risky)
            if llm.get("signals"):
                content_signals = list(llm["signals"])

    signals = list(struct.notes) + content_signals

    # Impersonation + risky content, or either one strongly on its own.
    force = False
    mtype = ""
    if struct.impersonation and content_risky:
        force, mtype = True, "scam"
    elif content_risky:
        force, mtype = True, "scam"
    elif struct.impersonation:
        force, mtype = True, "scam"

    if not force:
        return SafetyVerdict(s.message_id, False, signals=signals)

    reason = "Safety gate: " + "; ".join(signals[:3]) if signals else "Safety gate: risk detected"
    reason = re.sub(r"\s+", " ", reason).strip()
    confidence = 0.88 if (struct.impersonation and content_risky) else 0.83
    return SafetyVerdict(s.message_id, True, mtype, reason, confidence, signals)


# ─── Content-only LLM call ──────────────────────────────────────────────────

SAFETY_SYSTEM_PROMPT = """You are a safety classifier for WhatsApp messages.

You judge RISK ONLY. You are deliberately not told anything about how this
user has behaved toward this sender before, and you must not speculate about
it. Familiarity is not evidence of safety.

Flag risky=true when the message tries to obtain a credential (OTP, PIN, CVV,
password, card or wallet details), impersonates a brand or institution,
coerces via account loss or deadlines toward a verification/payment action, or
is otherwise deceptive.

Flag risky=false for messages that are merely promotional, repetitive, or
low-value. Being annoying is not being unsafe. Someone else decides whether
boring messages are worth showing.

Reply with JSON only: {"risky": bool, "signals": [str], "kind": "scam"|"spam"|"none"}"""


def render_safety_prompt(s: SafetyContext) -> str:
    lines = [
        f"conversation_type: {s.conversation_type}",
        f"forwarded_count: {s.forwarded_count}",
        f"message_text: {s.message_text!r}",
    ]
    if s.media_text:
        lines.append(f"media_{s.media_type}_extracted_text: {s.media_text!r}")
    if s.has_business_record:
        lines += [
            f"sender_display_name: {s.display_name}",
            f"sender_category: {s.category}",
            f"sender_verified: {s.verified}",
            f"official_domain: {s.official_domain or '(none registered)'}",
            f"domain_used_by_sender: {s.domain_used_by_sender or '(none)'}",
            f"account_age_days: {s.account_age_days}",
            f"domain_used_by_sender_age_days: {s.domain_used_by_sender_age_days}",
            f"reports_against_sender_30d: {s.user_reports_30d}",
        ]
    return "\n".join(lines)


def assert_blind(prompt: str) -> None:
    """Fail loudly if an engagement field ever reaches the gate's prompt."""
    leaked = sorted(f for f in FORBIDDEN_ENGAGEMENT_FIELDS if f in prompt)
    if leaked:
        raise AssertionError(
            f"Safety gate blindness violated: engagement fields in prompt: {leaked}"
        )


def _llm_safety(s: SafetyContext, provider: str) -> Optional[dict]:
    prompt = render_safety_prompt(s)
    assert_blind(prompt)

    cache = CACHE_DIR / provider / f"{s.message_id}.json"
    if cache.is_file():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))["parsed"]
        except (json.JSONDecodeError, KeyError, OSError):
            pass

    try:
        from router import _call_anthropic_raw, _call_nvidia_raw  # noqa: F401
    except ImportError:
        pass

    raw = _dispatch(provider, prompt)
    if raw is None:
        return None
    try:
        parsed = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
    except (AttributeError, json.JSONDecodeError):
        return None

    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"raw": raw, "parsed": parsed}, indent=2), encoding="utf-8")
    return parsed


def _dispatch(provider: str, prompt: str) -> Optional[str]:
    try:
        from net import post_json          # noqa: PLC0415
    except ImportError:
        from code.net import post_json     # noqa: PLC0415

    if provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        data = post_json(
            "https://api.anthropic.com/v1/messages",
            {
                "model": "claude-haiku-4-5",
                "max_tokens": 512,
                "temperature": 0,
                "system": SAFETY_SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": prompt}],
            },
            {"content-type": "application/json", "x-api-key": key,
             "anthropic-version": "2023-06-01"},
        )
        return "".join(b.get("text", "") for b in data.get("content", []))

    if provider == "nvidia":
        key = os.environ.get("NVIDIA_API_KEY")
        if not key:
            raise RuntimeError("NVIDIA_API_KEY is not set")
        base = os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
        data = post_json(
            f"{base}/chat/completions",
            {
                "model": os.environ.get("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct"),
                "temperature": 0, "max_tokens": 512,
                "messages": [{"role": "system", "content": SAFETY_SYSTEM_PROMPT},
                             {"role": "user", "content": prompt}],
            },
            {"content-type": "application/json", "authorization": f"Bearer {key}"},
        )
        return data["choices"][0]["message"]["content"]

    return None


def gate_all(contexts: list[MessageContext], provider: str = "stub") -> dict[str, SafetyVerdict]:
    return {c.message.message_id: safety_verdict(c, provider) for c in contexts}
